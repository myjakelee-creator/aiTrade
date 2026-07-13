from __future__ import annotations

import json
import time
from collections import deque
from statistics import median
from typing import Any

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, now_text
from realtime_v2.market_session import market_session_now


MOMENTUM_HOLD_PATH = RUNTIME_DIR / "theme_momentum_hold.json"
SAVE_INTERVAL_SEC = 5.0
HISTORY_WINDOW_SEC = 310.0
HOLD_PHASES = {"closed", "before_market", "weekend", "holiday"}


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _fmt_signed_pct(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%p"


def _fmt_ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}x"


def _load_hold_file() -> tuple[str, dict[str, dict[str, float]], dict[str, Any]]:
    try:
        payload = json.loads(MOMENTUM_HOLD_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return "", {}, {}
    if not isinstance(payload, dict):
        return "", {}, {}
    basis_date = str(payload.get("basis_trading_date") or "")
    raw_values = payload.get("values")
    values: dict[str, dict[str, float]] = {}
    if isinstance(raw_values, dict):
        for raw_theme_id, raw_entry in raw_values.items():
            if not isinstance(raw_entry, dict):
                continue
            entry: dict[str, float] = {}
            for key in ("change_momentum_1m", "change_persistence_5m"):
                value = _number(raw_entry.get(key))
                if value is not None:
                    entry[key] = round(value, 6)
            if entry:
                values[str(raw_theme_id)] = entry
    return basis_date, values, payload


def _session() -> dict[str, Any]:
    try:
        return market_session_now().to_dict()
    except Exception:
        return {
            "phase": "unknown",
            "accept_realtime": False,
            "is_trading_day": False,
            "trading_date": "",
        }


def install(theme_module) -> None:
    """Add cheap relative-strength features after the shared Theme projection.

    The patch stores only one average-change float per active theme per second for
    about five minutes. It does not read State, call OpenAPI/TR, rescore stocks, or
    add browser work. Existing Theme rows remain the single input and the current
    money-flow ranking is intentionally unchanged until the next approved stage.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_theme_momentum_installed", False):
        return

    original_init = builder_class.__init__
    original_call = builder_class.__call__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        basis_date, hold_values, metadata = _load_hold_file()
        self._theme_rate_history: dict[str, deque[tuple[float, float]]] = {}
        self._theme_momentum_basis_date = basis_date
        self._theme_momentum_hold_values = hold_values
        self._theme_momentum_hold_metadata = metadata
        self._theme_momentum_dirty = False
        self._theme_momentum_last_save_mono = 0.0

    def reset_history(self, trading_date: str) -> None:
        self._theme_rate_history.clear()
        self._theme_momentum_hold_values = {}
        self._theme_momentum_basis_date = str(trading_date or "")
        self._theme_momentum_dirty = True

    def update_history(
        self,
        theme_id: str,
        value: float | None,
        now_ts: float,
    ) -> None:
        if not theme_id or value is None:
            return
        history = self._theme_rate_history.setdefault(theme_id, deque())
        if history and now_ts - history[-1][0] < 0.9:
            history[-1] = (now_ts, value)
        else:
            history.append((now_ts, value))
        cutoff = now_ts - HISTORY_WINDOW_SEC
        while history and history[0][0] < cutoff:
            history.popleft()

    def delta(self, theme_id: str, now_ts: float, seconds: int) -> float | None:
        history = self._theme_rate_history.get(theme_id)
        if not history:
            return None
        target = now_ts - float(seconds)
        for point_ts, point_value in reversed(history):
            if point_ts <= target:
                return round(float(history[-1][1]) - float(point_value), 6)
        return None

    def remember(
        self,
        theme_id: str,
        one_min: float | None,
        five_min: float | None,
    ) -> None:
        if not theme_id:
            return
        entry = dict(self._theme_momentum_hold_values.get(theme_id) or {})
        changed = False
        if one_min is not None:
            value = round(one_min, 6)
            if entry.get("change_momentum_1m") != value:
                entry["change_momentum_1m"] = value
                changed = True
        if five_min is not None:
            value = round(five_min, 6)
            if entry.get("change_persistence_5m") != value:
                entry["change_persistence_5m"] = value
                changed = True
        if changed:
            self._theme_momentum_hold_values[theme_id] = entry
            self._theme_momentum_dirty = True

    def write_hold_if_needed(
        self,
        phase: str,
        valid_until: Any,
        *,
        force: bool = False,
    ) -> None:
        if not force and not self._theme_momentum_dirty:
            return
        now_mono = time.monotonic()
        if (
            not force
            and now_mono - float(self._theme_momentum_last_save_mono or 0.0)
            < SAVE_INTERVAL_SEC
        ):
            return
        values = dict(sorted(self._theme_momentum_hold_values.items()))
        atomic_write_json(
            MOMENTUM_HOLD_PATH,
            {
                "schema_version": 1,
                "source": "theme_projection_momentum_hold",
                "ts": now_text(),
                "basis_trading_date": self._theme_momentum_basis_date,
                "market_phase": phase,
                "valid_until": valid_until,
                "count": len(values),
                "values": values,
            },
        )
        self._theme_momentum_dirty = False
        self._theme_momentum_last_save_mono = now_mono

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        payload = original_call(self, feature_version, rows, meta)
        if not isinstance(payload, dict) or payload.get("status") != "READY":
            return payload

        details = payload.get("details")
        summaries = payload.get("rows")
        if not isinstance(details, dict) or not isinstance(summaries, list):
            return payload

        meta = meta if isinstance(meta, dict) else {}
        status_meta = meta.get("status") if isinstance(meta.get("status"), dict) else {}
        session = _session()
        flow_status = (
            payload.get("flow_history_status")
            if isinstance(payload.get("flow_history_status"), dict)
            else {}
        )
        continuity_status = (
            payload.get("metric_continuity_status")
            if isinstance(payload.get("metric_continuity_status"), dict)
            else {}
        )
        phase = str(
            flow_status.get("market_phase")
            or continuity_status.get("phase")
            or status_meta.get("metric_continuity_phase")
            or session.get("phase")
            or "unknown"
        ).lower()
        trading_date = str(
            flow_status.get("trading_date")
            or continuity_status.get("reference_date")
            or status_meta.get("metric_continuity_reference_date")
            or meta.get("trading_date")
            or status_meta.get("trading_date")
            or session.get("trading_date")
            or ""
        )
        valid_until = (
            flow_status.get("valid_until")
            or continuity_status.get("valid_until")
            or status_meta.get("metric_continuity_valid_until")
        )
        now_ts = _number(meta.get("snapshot_epoch")) or time.time()
        hold_active = phase in HOLD_PHASES or not bool(
            session.get("accept_realtime", True)
        )
        new_session_zero = phase in {"premarket", "opening_call"}

        if (
            trading_date
            and self._theme_momentum_basis_date
            and trading_date != self._theme_momentum_basis_date
        ):
            reset_history(self, trading_date)
        elif trading_date and not self._theme_momentum_basis_date:
            self._theme_momentum_basis_date = trading_date

        summary_by_id = {
            str(item.get("theme_id") or ""): item
            for item in summaries
            if isinstance(item, dict)
        }
        one_ready = 0
        five_ready = 0
        amount_ratio_ready = 0
        held_count = 0

        for raw_theme_id, detail in details.items():
            if not isinstance(detail, dict):
                continue
            theme_id = str(detail.get("theme_id") or raw_theme_id)
            members = detail.get("members")
            members = members if isinstance(members, list) else []
            rates = [
                value
                for value in (_number(member.get("change_rate")) for member in members if isinstance(member, dict))
                if value is not None
            ]
            ratios = [
                value
                for value in (_number(member.get("amount_ratio")) for member in members if isinstance(member, dict))
                if value is not None and value > 0
            ]
            median_rate = float(median(rates)) if rates else None
            amount_ratio = float(median(ratios)) if ratios else None
            amount_ratio_coverage = (
                round(len(ratios) / len(members) * 100.0, 2) if members else 0.0
            )
            average_rate = _number(detail.get("avg_change_rate"))

            if not hold_active:
                update_history(self, theme_id, average_rate, now_ts)
            one_min = delta(self, theme_id, now_ts, 60)
            five_min = delta(self, theme_id, now_ts, 300)
            basis = "live_history"

            cached = self._theme_momentum_hold_values.get(theme_id) or {}
            if hold_active:
                if one_min is None:
                    one_min = _number(cached.get("change_momentum_1m"))
                if five_min is None:
                    five_min = _number(cached.get("change_persistence_5m"))
                basis = "last_session_hold"
                held_count += int(one_min is not None or five_min is not None)
            elif new_session_zero:
                if one_min is None:
                    one_min = 0.0
                if five_min is None:
                    five_min = 0.0
                basis = "new_session_wait"
            elif one_min is None or five_min is None:
                if one_min is None:
                    one_min = _number(cached.get("change_momentum_1m"))
                if five_min is None:
                    five_min = _number(cached.get("change_persistence_5m"))
                if one_min is not None or five_min is not None:
                    basis = "restart_hold_until_history_ready"
                    held_count += 1
                else:
                    basis = "history_warmup"

            if not hold_active:
                remember(self, theme_id, one_min, five_min)

            feature_values = {
                "median_change_rate": round(median_rate, 4) if median_rate is not None else None,
                "median_change_rate_text": (
                    "-" if median_rate is None else f"{median_rate:+.2f}%"
                ),
                "change_momentum_1m": round(one_min, 4) if one_min is not None else None,
                "change_momentum_1m_text": _fmt_signed_pct(one_min),
                "change_persistence_5m": round(five_min, 4) if five_min is not None else None,
                "change_persistence_5m_text": _fmt_signed_pct(five_min),
                "theme_amount_ratio": round(amount_ratio, 4) if amount_ratio is not None else None,
                "theme_amount_ratio_text": _fmt_ratio(amount_ratio),
                "amount_ratio_member_count": len(ratios),
                "amount_ratio_coverage_pct": amount_ratio_coverage,
                "amount_ratio_coverage_text": f"{amount_ratio_coverage:.0f}%",
                "trend_feature_basis": basis,
            }
            detail.update(feature_values)
            summary = summary_by_id.get(theme_id)
            if isinstance(summary, dict):
                summary.update(feature_values)

            one_ready += int(one_min is not None)
            five_ready += int(five_min is not None)
            amount_ratio_ready += int(amount_ratio is not None)

        write_hold_if_needed(self, phase, valid_until)

        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["trend_feature_input"] = "theme_projection_completed_rows_only"
            policy["theme_amount_ratio"] = "median_positive_member_amount_ratio"
            policy["change_momentum_history"] = "theme_average_change_delta_60s_300s"
            policy["additional_tr_allowed"] = False
            policy["browser_feature_calculation_allowed"] = False

        payload["trend_feature_status"] = {
            "enabled": True,
            "active_theme_count": len(details),
            "history_theme_count": len(self._theme_rate_history),
            "one_min_ready_count": one_ready,
            "five_min_ready_count": five_ready,
            "amount_ratio_ready_count": amount_ratio_ready,
            "held_theme_count": held_count,
            "market_phase": phase,
            "trading_date": self._theme_momentum_basis_date or trading_date or None,
            "hold_active": hold_active,
            "valid_until": valid_until,
            "path": str(MOMENTUM_HOLD_PATH),
            "memory_policy": "one_float_per_active_theme_per_second_310s",
        }
        payload["calculate_ms"] = round(
            (time.perf_counter() - total_started) * 1000.0,
            3,
        )
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class._stockboard_theme_momentum_installed = True
