from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from realtime_v2 import worker64 as worker_base
from realtime_v2.board_metric_continuity_patch import (
    install as install_board_metric_continuity,
)
from realtime_v2.common import RUNTIME_DIR, atomic_write_json, now_text
from realtime_v2.market_session import market_session_now


# This module is imported only after the existing StockBoard metric hold patches
# have been installed. Wrapping State.rows here makes continuity the final common
# read layer before BoardDataHub publishes one FeatureSnapshot to every board.
install_board_metric_continuity(worker_base)

FLOW_HOLD_PATH = RUNTIME_DIR / "theme_flow_history_hold.json"
FLOW_SAVE_INTERVAL_SEC = 5.0
HOLD_PHASES = {"closed", "before_market", "weekend", "holiday"}


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _code(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text[-6:] if len(text) >= 6 else ""


def _load_hold_file() -> tuple[str, dict[str, dict[str, float]], dict[str, Any]]:
    try:
        payload = json.loads(FLOW_HOLD_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return "", {}, {}
    if not isinstance(payload, dict):
        return "", {}, {}
    basis_date = str(payload.get("basis_trading_date") or "")
    values: dict[str, dict[str, float]] = {}
    raw_values = payload.get("values")
    if isinstance(raw_values, dict):
        for raw_code, raw_entry in raw_values.items():
            code = _code(raw_code)
            if not code or not isinstance(raw_entry, dict):
                continue
            one = _number(raw_entry.get("trade_value_1m_eok"))
            five = _number(raw_entry.get("trade_value_5m_eok"))
            entry: dict[str, float] = {}
            if one is not None and one >= 0:
                entry["trade_value_1m_eok"] = round(one, 4)
            if five is not None and five >= 0:
                entry["trade_value_5m_eok"] = round(five, 4)
            if entry:
                values[code] = entry
    return basis_date, values, payload


def _session() -> dict[str, Any]:
    try:
        return market_session_now().to_dict()
    except Exception:
        return {
            "phase": "unknown",
            "accept_realtime": False,
            "is_trading_day": False,
        }


def install(theme_module) -> None:
    """Derive and persist 1m/5m amounts from shared cumulative values.

    This patch never reads State, OpenAPI, TR, or browser data. It augments the
    latest shared FeatureSnapshot rows before ThemeProjectionBuilder aggregates
    them. The last completed 1m/5m values are held across close, reconnect,
    weekends, holidays and delayed-open before-market periods. A new actual
    premarket starts a new basis date and displays explicit zero until enough new
    history is accumulated.
    """

    from realtime_v2.theme_projection_continuity_guard_patch import (
        install as install_theme_projection_continuity_guard,
    )

    install_theme_projection_continuity_guard(theme_module)

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_flow_history_installed", False):
        return

    original_init = builder_class.__init__
    original_call = builder_class.__call__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        basis_date, hold_values, metadata = _load_hold_file()
        self._flow_history_by_code: dict[str, deque[tuple[float, float]]] = {}
        self._flow_last_value_by_code: dict[str, float] = {}
        self._flow_hold_values_by_code: dict[str, dict[str, float]] = hold_values
        self._flow_trading_date = basis_date
        self._flow_hold_metadata = metadata
        self._flow_dirty = False
        self._flow_last_save_mono = 0.0

    def reset_history(self, trading_date: str = "") -> None:
        self._flow_history_by_code.clear()
        self._flow_last_value_by_code.clear()
        self._flow_hold_values_by_code = {}
        self._flow_trading_date = str(trading_date or "")
        self._flow_dirty = True

    def update_history(
        self,
        rows: tuple[dict[str, Any], ...],
        now_ts: float,
        trading_date: str,
    ) -> None:
        if (
            trading_date
            and self._flow_trading_date
            and trading_date != self._flow_trading_date
        ):
            reset_history(self, trading_date)
        elif trading_date and not self._flow_trading_date:
            self._flow_trading_date = trading_date

        cutoff = now_ts - 310.0
        for row in rows:
            code = _code(row.get("stock_code"))
            cumulative = _number(row.get("trade_value_eok"))
            if not code or cumulative is None or cumulative < 0:
                continue
            history = self._flow_history_by_code.setdefault(code, deque())
            previous = self._flow_last_value_by_code.get(code)
            if previous is not None and cumulative + 1e-9 < previous:
                history.clear()
            if history and int(history[-1][0]) == int(now_ts):
                history[-1] = (now_ts, cumulative)
            else:
                history.append((now_ts, cumulative))
            while history and history[0][0] < cutoff:
                history.popleft()
            self._flow_last_value_by_code[code] = cumulative

    def delta(self, code: str, now_ts: float, seconds: int) -> float | None:
        history = self._flow_history_by_code.get(code)
        if not history:
            return None
        target = now_ts - float(seconds)
        candidate: tuple[float, float] | None = None
        for point in reversed(history):
            if point[0] <= target:
                candidate = point
                break
        if candidate is None:
            return None
        return max(0.0, float(history[-1][1]) - float(candidate[1]))

    def remember_flow(
        self,
        code: str,
        one_min: float | None,
        five_min: float | None,
    ) -> None:
        if not code:
            return
        entry = dict(self._flow_hold_values_by_code.get(code) or {})
        changed = False
        if one_min is not None and one_min >= 0:
            value = round(one_min, 4)
            if entry.get("trade_value_1m_eok") != value:
                entry["trade_value_1m_eok"] = value
                changed = True
        if five_min is not None and five_min >= 0:
            value = round(five_min, 4)
            if entry.get("trade_value_5m_eok") != value:
                entry["trade_value_5m_eok"] = value
                changed = True
        if changed:
            self._flow_hold_values_by_code[code] = entry
            self._flow_dirty = True

    def write_hold_if_needed(
        self,
        phase: str,
        valid_until: Any,
        *,
        force: bool = False,
    ) -> None:
        if not force and not self._flow_dirty:
            return
        now_mono = time.monotonic()
        if (
            not force
            and now_mono - float(self._flow_last_save_mono or 0.0)
            < FLOW_SAVE_INTERVAL_SEC
        ):
            return
        values = dict(sorted(self._flow_hold_values_by_code.items()))
        atomic_write_json(
            FLOW_HOLD_PATH,
            {
                "schema_version": 1,
                "source": "theme_projection_flow_hold",
                "ts": now_text(),
                "basis_trading_date": self._flow_trading_date,
                "market_phase": phase,
                "valid_until": valid_until,
                "count": len(values),
                "values": values,
            },
        )
        self._flow_dirty = False
        self._flow_last_save_mono = now_mono

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        meta = meta if isinstance(meta, dict) else {}
        status_meta = meta.get("status") if isinstance(meta.get("status"), dict) else {}
        session = _session()
        phase = str(
            status_meta.get("metric_continuity_phase")
            or session.get("phase")
            or "unknown"
        ).lower()
        hold_active = phase in HOLD_PHASES or not bool(
            session.get("accept_realtime", True)
        )
        trading_date = str(
            status_meta.get("metric_continuity_reference_date")
            or meta.get("trading_date")
            or status_meta.get("trading_date")
            or ""
        )
        valid_until = status_meta.get("metric_continuity_valid_until")
        now_ts = _number(meta.get("snapshot_epoch")) or time.time()

        if (
            trading_date
            and self._flow_trading_date
            and trading_date != self._flow_trading_date
        ):
            reset_history(self, trading_date)
        elif trading_date and not self._flow_trading_date:
            self._flow_trading_date = trading_date

        if not hold_active:
            update_history(self, rows, now_ts, trading_date)

        projected_rows: list[dict[str, Any]] = []
        one_ready = 0
        five_ready = 0
        held_count = 0
        new_session_zero = phase in {"premarket", "opening_call"}

        for row in rows:
            code = _code(row.get("stock_code"))
            one_min = _number(row.get("trade_value_1m_eok"))
            five_min = _number(row.get("trade_value_5m_eok"))
            basis = "feature_snapshot"

            if one_min is None and not hold_active:
                one_min = delta(self, code, now_ts, 60)
            if five_min is None and not hold_active:
                five_min = delta(self, code, now_ts, 300)

            if not hold_active:
                remember_flow(self, code, one_min, five_min)

            cached = self._flow_hold_values_by_code.get(code) or {}
            if hold_active:
                if one_min is None:
                    one_min = _number(cached.get("trade_value_1m_eok"))
                if five_min is None:
                    five_min = _number(cached.get("trade_value_5m_eok"))
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
                    one_min = _number(cached.get("trade_value_1m_eok"))
                if five_min is None:
                    five_min = _number(cached.get("trade_value_5m_eok"))
                if one_min is not None or five_min is not None:
                    basis = "restart_hold_until_history_ready"
                    held_count += 1

            if one_min is None and five_min is None:
                projected_rows.append(row)
                continue
            next_row = dict(row)
            if one_min is not None:
                next_row["trade_value_1m_eok"] = round(one_min, 4)
                one_ready += 1
            if five_min is not None:
                next_row["trade_value_5m_eok"] = round(five_min, 4)
                five_ready += 1
            next_row["theme_flow_display_basis"] = basis
            projected_rows.append(next_row)

        write_hold_if_needed(self, phase, valid_until)

        payload = original_call(
            self,
            feature_version,
            tuple(projected_rows),
            meta,
        )
        if isinstance(payload, dict):
            policy = payload.setdefault("policy", {})
            if isinstance(policy, dict):
                policy["flow_history"] = "server_cumulative_delta_60s_300s"
                policy["flow_hold"] = "persist_until_next_actual_premarket"
            payload["flow_history_status"] = {
                "tracked_code_count": len(self._flow_history_by_code),
                "held_code_count": held_count,
                "one_min_ready_count": one_ready,
                "five_min_ready_count": five_ready,
                "trading_date": self._flow_trading_date or trading_date or None,
                "market_phase": phase,
                "hold_active": hold_active,
                "valid_until": valid_until,
                "path": str(FLOW_HOLD_PATH),
            }
            payload["metric_continuity_status"] = {
                "enabled": bool(status_meta.get("metric_continuity_enabled")),
                "phase": status_meta.get("metric_continuity_phase") or phase,
                "reference_date": status_meta.get(
                    "metric_continuity_reference_date"
                ),
                "valid_until": valid_until,
                "cache_count": status_meta.get("metric_continuity_cache_count"),
                "applied_rows": status_meta.get(
                    "metric_continuity_applied_rows"
                ),
                "scoring_blocked_rows": status_meta.get(
                    "metric_continuity_scoring_blocked_rows"
                ),
            }
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class._stockboard_flow_history_installed = True
