from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "metric_state_overlay_v1"

CANONICAL_DATE_KEYS = {
    "amount_ratio": "amount_ratio_source_trading_date",
    "orderbook": "orderbook_source_trading_date",
    "execution": "execution_source_trading_date",
    "strength5": "strength_source_trading_date",
    "program": "program_source_trading_date",
    "large_trade": "large_trade_source_trading_date",
}


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _install_canonical_date_keys(lifecycle) -> None:
    """Make source-trading-date fields authoritative regardless of patch order."""

    for group, key in CANONICAL_DATE_KEYS.items():
        config = lifecycle.GROUPS.get(group)
        if not isinstance(config, dict):
            continue
        config["capture_keys"] = tuple(
            dict.fromkeys((key, *tuple(config.get("capture_keys") or ())))
        )
        config["date_keys"] = tuple(
            dict.fromkeys((key, *tuple(config.get("date_keys") or ())))
        )


def install(base) -> None:
    """Overlay current metric state before lifecycle and output guards run.

    Background heavy snapshots intentionally avoid rebuilding on every REST result.
    Therefore an HTTP/SSE row can lag behind the authoritative in-memory metric state
    even though the REST request and daily-state write succeeded. This wrapper copies
    only the six approved metric fields from ``daily_values_by_code`` and ``quotes``
    into the outgoing rows. It performs no collection, calculation thread, network
    request, QAx registration, or price-field mutation.
    """

    import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_metric_state_overlay_installed",
        False,
    ):
        return

    _install_canonical_date_keys(lifecycle)

    approved_keys: set[str] = {
        "trade_value_eok",
        "prev_trade_value_eok",
        "amount_ratio",
        "amount_ratio_source",
        "amount_ratio_status",
        "amount_ratio_updated_at",
        "_session_hold_amount_ratio_date",
    }
    for config in lifecycle.GROUPS.values():
        approved_keys.update(config.get("display_keys") or ())
        approved_keys.update(config.get("capture_keys") or ())
        approved_keys.update(config.get("date_keys") or ())
        source_key = config.get("source_key")
        status_key = config.get("status_key")
        if source_key:
            approved_keys.add(str(source_key))
        if status_key:
            approved_keys.add(str(status_key))

    original_state_init = state_class.__init__
    original_rows = state_class.rows

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["metric_state_overlay_installed"] = True
            self.status["metric_state_overlay_version"] = PATCH_VERSION

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        if not isinstance(result, list) or not result:
            return result

        codes = {
            normalize_code(row.get("stock_code"))
            for row in result
            if isinstance(row, dict) and normalize_code(row.get("stock_code"))
        }
        with self.lock:
            state_date = str(self.status.get("metric_session_state_date") or "")
            authoritative: dict[str, dict[str, Any]] = {}
            for code in codes:
                merged: dict[str, Any] = {}
                daily = self.daily_values_by_code.get(code)
                quote = self.quotes.get(code)
                if isinstance(daily, dict):
                    merged.update(daily)
                if isinstance(quote, dict):
                    merged.update(quote)
                authoritative[code] = {
                    key: deepcopy(value)
                    for key, value in merged.items()
                    if key in approved_keys and value not in (None, "")
                }

        now = datetime.now()
        session = market_session_now(now)
        expected = lifecycle._expected_date(session, now)
        overlaid_rows = 0
        amount_ratio_stamped = 0
        group_overlays = {group: 0 for group in lifecycle.GROUPS}

        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            if not code:
                continue
            source = authoritative.get(code) or {}
            changed = False
            for key, value in source.items():
                if row.get(key) != value:
                    row[key] = deepcopy(value)
                    changed = True

            prepared = lifecycle._prepare_amount_ratio(row)
            row.update(prepared)
            ratio = _number(row.get("amount_ratio"))
            if (
                ratio is not None
                and ratio > 0
                and not lifecycle._date_digits(
                    row.get(CANONICAL_DATE_KEYS["amount_ratio"])
                )
                and expected
                and state_date == expected
            ):
                row[CANONICAL_DATE_KEYS["amount_ratio"]] = expected
                row["_session_hold_amount_ratio_date"] = expected
                amount_ratio_stamped += 1
                changed = True

            if changed:
                overlaid_rows += 1
            for group in lifecycle.GROUPS:
                if lifecycle._group_usable(row, group):
                    group_overlays[group] += 1

        with self.lock:
            self.status["metric_state_overlay_expected_date"] = expected or None
            self.status["metric_state_overlay_state_date"] = state_date or None
            self.status["metric_state_overlay_row_count"] = overlaid_rows
            self.status["metric_state_overlay_amount_ratio_stamped_count"] = (
                amount_ratio_stamped
            )
            for group, count in group_overlays.items():
                self.status[f"metric_state_overlay_{group}_count"] = count
        return result

    state_class.__init__ = state_init
    state_class.rows = rows
    state_class._stockboard_metric_state_overlay_installed = True
