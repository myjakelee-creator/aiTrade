from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, now_text
from realtime_v2.market_session import market_session_now


HOLD_PATH = RUNTIME_DIR / "theme_momentum_hold.json"
HOLD_PHASES = {"closed", "before_market", "weekend", "holiday"}
HISTORY_WINDOW_SEC = 310.0
SAVE_INTERVAL_SEC = 5.0


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _load() -> tuple[str, dict[str, dict[str, float]]]:
    try:
        payload = json.loads(HOLD_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return "", {}
    if not isinstance(payload, dict):
        return "", {}
    values: dict[str, dict[str, float]] = {}
    raw_values = payload.get("values")
    if isinstance(raw_values, dict):
        for raw_theme_id, raw_entry in raw_values.items():
            if not isinstance(raw_entry, dict):
                continue
            entry: dict[str, float] = {}
            for key in ("change_momentum_1m", "change_persistence_5m"):
                value = _number(raw_entry.get(key))
                if value is not None:
                    entry[key] = value
            if entry:
                values[str(raw_theme_id)] = entry
    return str(payload.get("basis_trading_date") or ""), values


def _session(payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    flow = (
        payload.get("flow_history_status")
        if isinstance(payload.get("flow_history_status"), dict)
        else {}
    )
    continuity = (
        payload.get("metric_continuity_status")
        if isinstance(payload.get("metric_continuity_status"), dict)
        else {}
    )
    status = meta.get("status") if isinstance(meta.get("status"), dict) else {}
    try:
        current = market_session_now().to_dict()
    except Exception:
        current = {
            "phase": "unknown",
            "accept_realtime": False,
            "trading_date": "",
        }
    phase = str(
        flow.get("market_phase")
        or continuity.get("phase")
        or status.get("metric_continuity_phase")
        or current.get("phase")
        or "unknown"
    ).lower()
    trading_date = str(
        flow.get("trading_date")
        or continuity.get("reference_date")
        or status.get("metric_continuity_reference_date")
        or meta.get("trading_date")
        or current.get("trading_date")
        or ""
    )
    valid_until = (
        flow.get("valid_until")
        or continuity.get("valid_until")
        or status.get("metric_continuity_valid_until")
    )
    hold_active = phase in HOLD_PHASES or not bool(
        current.get("accept_realtime", True)
    )
    return {
        "phase": phase,
        "trading_date": trading_date,
        "valid_until": valid_until,
        "hold_active": hold_active,
        "new_session_zero": phase in {"premarket", "opening_call"},
    }


def install(theme_module) -> None:
    """Add 1m/5m change-rate history to summary rows only."""

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_summary_momentum_installed", False):
        return

    original_init = builder_class.__init__
    original_call = builder_class.__call__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        basis_date, hold_values = _load()
        self._summary_momentum_lock = threading.RLock()
        self._summary_rate_history: dict[str, deque[tuple[float, float]]] = {}
        self._summary_momentum_basis_date = basis_date
        self._summary_momentum_hold = hold_values
        self._summary_momentum_dirty = False
        self._summary_momentum_last_save_mono = 0.0
        self._summary_momentum_save_wakeup = threading.Event()
        self._summary_momentum_save_thread = threading.Thread(
            target=save_loop,
            args=(self,),
            name="theme-summary-momentum-persist",
            daemon=True,
        )
        self._summary_momentum_save_thread.start()

    def save_loop(self) -> None:
        while True:
            self._summary_momentum_save_wakeup.wait(timeout=1.0)
            self._summary_momentum_save_wakeup.clear()
            now_mono = time.monotonic()
            with self._summary_momentum_lock:
                if not self._summary_momentum_dirty:
                    continue
                if (
                    now_mono - self._summary_momentum_last_save_mono
                    < SAVE_INTERVAL_SEC
                ):
                    continue
                values = dict(self._summary_momentum_hold)
                basis_date = self._summary_momentum_basis_date
                phase = getattr(self, "_summary_momentum_phase", "unknown")
                valid_until = getattr(self, "_summary_momentum_valid_until", None)
            try:
                atomic_write_json(
                    HOLD_PATH,
                    {
                        "schema_version": 1,
                        "source": "theme_summary_momentum_hold",
                        "ts": now_text(),
                        "basis_trading_date": basis_date,
                        "market_phase": phase,
                        "valid_until": valid_until,
                        "count": len(values),
                        "values": dict(sorted(values.items())),
                    },
                )
            except Exception:
                continue
            with self._summary_momentum_lock:
                self._summary_momentum_dirty = False
                self._summary_momentum_last_save_mono = time.monotonic()

    def delta(
        history: deque[tuple[float, float]],
        now_ts: float,
        seconds: int,
    ) -> float | None:
        target = now_ts - float(seconds)
        for point_ts, point_value in reversed(history):
            if point_ts <= target:
                return float(history[-1][1]) - float(point_value)
        return None

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

        summaries = payload.get("rows")
        if not isinstance(summaries, list):
            return payload

        info = _session(payload, meta if isinstance(meta, dict) else {})
        now_ts = _number(meta.get("snapshot_epoch")) or time.time()
        phase = str(info.get("phase") or "unknown")
        trading_date = str(info.get("trading_date") or "")
        hold_active = bool(info.get("hold_active"))
        new_session_zero = bool(info.get("new_session_zero"))
        started = time.perf_counter()

        with self._summary_momentum_lock:
            if (
                trading_date
                and self._summary_momentum_basis_date
                and trading_date != self._summary_momentum_basis_date
            ):
                self._summary_rate_history.clear()
                self._summary_momentum_hold = {}
                self._summary_momentum_basis_date = trading_date
                self._summary_momentum_dirty = True
            elif trading_date and not self._summary_momentum_basis_date:
                self._summary_momentum_basis_date = trading_date

            one_ready = five_ready = held_count = 0
            cutoff = now_ts - HISTORY_WINDOW_SEC
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                theme_id = str(summary.get("theme_id") or "")
                average_rate = _number(summary.get("avg_change_rate"))
                history = self._summary_rate_history.setdefault(theme_id, deque())
                if not hold_active and average_rate is not None:
                    if history and now_ts - history[-1][0] < 0.9:
                        history[-1] = (now_ts, average_rate)
                    else:
                        history.append((now_ts, average_rate))
                    while history and history[0][0] < cutoff:
                        history.popleft()

                one = delta(history, now_ts, 60) if history else None
                five = delta(history, now_ts, 300) if history else None
                cached = self._summary_momentum_hold.get(theme_id) or {}
                basis = "live_history"
                if hold_active:
                    if one is None:
                        one = _number(cached.get("change_momentum_1m"))
                    if five is None:
                        five = _number(cached.get("change_persistence_5m"))
                    basis = "last_session_hold"
                    held_count += int(one is not None or five is not None)
                elif new_session_zero:
                    if one is None:
                        one = 0.0
                    if five is None:
                        five = 0.0
                    basis = "new_session_wait"
                elif one is None or five is None:
                    if one is None:
                        one = _number(cached.get("change_momentum_1m"))
                    if five is None:
                        five = _number(cached.get("change_persistence_5m"))
                    basis = (
                        "restart_hold_until_history_ready"
                        if one is not None or five is not None
                        else "history_warmup"
                    )
                    held_count += int(one is not None or five is not None)

                if not hold_active and (one is not None or five is not None):
                    entry = dict(cached)
                    if one is not None:
                        entry["change_momentum_1m"] = round(one, 6)
                    if five is not None:
                        entry["change_persistence_5m"] = round(five, 6)
                    if entry != cached:
                        self._summary_momentum_hold[theme_id] = entry
                        self._summary_momentum_dirty = True

                summary.update(
                    {
                        "change_momentum_1m": (
                            round(one, 4) if one is not None else None
                        ),
                        "change_momentum_1m_text": (
                            "-" if one is None else f"{one:+.2f}%p"
                        ),
                        "change_persistence_5m": (
                            round(five, 4) if five is not None else None
                        ),
                        "change_persistence_5m_text": (
                            "-" if five is None else f"{five:+.2f}%p"
                        ),
                        "trend_feature_basis": basis,
                    }
                )
                one_ready += int(one is not None)
                five_ready += int(five is not None)

            self._summary_momentum_phase = phase
            self._summary_momentum_valid_until = info.get("valid_until")
            if self._summary_momentum_dirty:
                self._summary_momentum_save_wakeup.set()

        momentum_ms = (time.perf_counter() - started) * 1000.0
        total_ms = (time.perf_counter() - total_started) * 1000.0
        performance = payload.setdefault("performance_breakdown", {})
        if isinstance(performance, dict):
            performance["momentum_ms"] = round(momentum_ms, 3)
            performance["total_ms"] = round(total_ms, 3)
            performance["other_ms"] = round(
                max(
                    0.0,
                    total_ms
                    - float(performance.get("aggregate_ms") or 0.0)
                    - float(performance.get("score_sort_ms") or 0.0)
                    - momentum_ms,
                ),
                3,
            )
        payload["calculate_ms"] = round(total_ms, 3)
        payload["trend_feature_status"] = {
            "enabled": True,
            "active_theme_count": len(summaries),
            "history_theme_count": len(self._summary_rate_history),
            "one_min_ready_count": one_ready,
            "five_min_ready_count": five_ready,
            "amount_ratio_ready_count": sum(
                int(
                    isinstance(row, dict)
                    and row.get("theme_amount_ratio") is not None
                )
                for row in summaries
            ),
            "held_theme_count": held_count,
            "market_phase": phase,
            "trading_date": self._summary_momentum_basis_date
            or trading_date
            or None,
            "hold_active": hold_active,
            "valid_until": info.get("valid_until"),
            "path": str(HOLD_PATH),
            "memory_policy": "one_float_per_active_theme_per_second_310s",
            "precomputed_member_stats_enabled": True,
            "member_rescan_theme_count": 0,
            "summary_member_projection_count": 0,
            "persistence_mode": "background_coalesced_5s",
        }
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["trend_feature_input"] = "theme_summary_rows_only"
            policy["theme_amount_ratio"] = "median_positive_member_amount_ratio"
            policy["change_momentum_history"] = (
                "theme_average_change_delta_60s_300s"
            )
            policy["additional_tr_allowed"] = False
            policy["browser_feature_calculation_allowed"] = False
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class._stockboard_summary_momentum_installed = True
