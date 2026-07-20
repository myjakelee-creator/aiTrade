from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, to_number

ORDERBOOK_METADATA_KEYS = (
    "orderbook_source",
    "orderbook_status",
    "orderbook_received_at",
    "orderbook_display_basis",
)
EXECUTION_METADATA_KEYS = (
    "execution_strength_source",
    "execution_strength_status",
    "execution_strength_updated_at",
)
STRENGTH_METADATA_KEYS = (
    "strength_source",
    "strength_status",
    "strength_snapshot_at",
)


def _copy_missing(target: dict[str, Any], source: dict[str, Any], keys: tuple[str, ...]) -> int:
    copied = 0
    for key in keys:
        if target.get(key) not in (None, ""):
            continue
        value = source.get(key)
        if value in (None, ""):
            continue
        target[key] = deepcopy(value)
        copied += 1
    return copied


def _positive(value: Any) -> bool:
    number = to_number(value)
    return number is not None and float(number) > 0


def install(base) -> None:
    """Preserve source/status timestamps after hold/cache row projection.

    The low-load REST updater already writes provenance into the canonical quote and
    daily state. Older bidask/session-hold wrappers copied metric values but omitted
    some metadata fields. This final row wrapper restores only those missing strings;
    it performs no network request, calculation, sorting, or browser work.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_rest_metric_metadata_installed", False
    ):
        return

    original_rows = state_class.rows

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        codes = {
            normalize_code(row.get("stock_code"))
            for row in result
            if isinstance(row, dict)
        }
        codes.discard("")
        with self.lock:
            daily = {
                code: dict(self.daily_values_by_code.get(code) or {})
                for code in codes
            }
            quotes = {
                code: dict(self.quotes.get(code) or {})
                for code in codes
            }

        restored_rows = 0
        restored_fields = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            if not code:
                continue
            sources = (daily.get(code, {}), quotes.get(code, {}))
            copied = 0
            if _positive(row.get("bid_ask_ratio")):
                for source in sources:
                    copied += _copy_missing(row, source, ORDERBOOK_METADATA_KEYS)
            if _positive(row.get("execution_strength")):
                for source in sources:
                    copied += _copy_missing(row, source, EXECUTION_METADATA_KEYS)
            if _positive(row.get("strength_5m")):
                for source in sources:
                    copied += _copy_missing(row, source, STRENGTH_METADATA_KEYS)
            if copied:
                restored_rows += 1
                restored_fields += copied

        with self.lock:
            self.status["rest_metric_metadata_restored_rows"] = restored_rows
            self.status["rest_metric_metadata_restored_fields"] = restored_fields
        return result

    state_class.rows = rows
    state_class._stockboard_rest_metric_metadata_installed = True
