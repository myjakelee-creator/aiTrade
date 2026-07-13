from __future__ import annotations

import time
from collections import deque
from typing import Any


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


def install(theme_module) -> None:
    """Derive 1m/5m amounts from shared cumulative values once per projection.

    This patch never reads State, OpenAPI, TR, or browser data. It augments the
    latest shared FeatureSnapshot rows before the existing ThemeProjectionBuilder
    aggregates them. The builder still runs at its one-second latest-only cadence.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_flow_history_installed", False):
        return

    original_init = builder_class.__init__
    original_call = builder_class.__call__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._flow_history_by_code: dict[str, deque[tuple[float, float]]] = {}
        self._flow_last_value_by_code: dict[str, float] = {}
        self._flow_trading_date = ""

    def reset_history(self, trading_date: str = "") -> None:
        self._flow_history_by_code.clear()
        self._flow_last_value_by_code.clear()
        self._flow_trading_date = str(trading_date or "")

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

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        meta = meta if isinstance(meta, dict) else {}
        status_meta = meta.get("status") if isinstance(meta.get("status"), dict) else {}
        trading_date = str(
            meta.get("trading_date")
            or status_meta.get("trading_date")
            or ""
        )
        now_ts = _number(meta.get("snapshot_epoch")) or time.time()
        update_history(self, rows, now_ts, trading_date)

        projected_rows: list[dict[str, Any]] = []
        one_ready = 0
        five_ready = 0
        for row in rows:
            code = _code(row.get("stock_code"))
            one_min = _number(row.get("trade_value_1m_eok"))
            five_min = _number(row.get("trade_value_5m_eok"))
            if one_min is None:
                one_min = delta(self, code, now_ts, 60)
            if five_min is None:
                five_min = delta(self, code, now_ts, 300)

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
            projected_rows.append(next_row)

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
            payload["flow_history_status"] = {
                "tracked_code_count": len(self._flow_history_by_code),
                "one_min_ready_count": one_ready,
                "five_min_ready_count": five_ready,
                "trading_date": self._flow_trading_date or trading_date or None,
            }
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class._stockboard_flow_history_installed = True
