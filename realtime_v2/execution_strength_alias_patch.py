from __future__ import annotations

from copy import deepcopy
from typing import Any

PATCH_VERSION = "execution_strength_source_separation_v1"
TRUSTED_EXECUTION_SOURCES = frozenset(
    {
        "kiwoom_rest_ws_0b_fid228",
        "kiwoom_rest_ws_0b_fid228_close_hold",
    }
)
EXECUTION_FIELDS = (
    "execution_strength",
    "last_valid_execution_strength",
    "ui_execution_strength",
    "execution_strength_received_at",
    "execution_strength_updated_at",
    "ui_execution_strength_observed_at",
    "execution_strength_source_time",
    "execution_strength_exchange",
    "execution_strength_market_phase",
    "execution_strength_trade_price",
    "execution_strength_source",
    "execution_strength_status",
    "execution_source_trading_date",
    "ui_execution_source_trading_date",
    "_session_hold_execution_date",
)


def execution_source_trusted(values: dict[str, Any] | None) -> bool:
    if not isinstance(values, dict):
        return False
    source = str(values.get("execution_strength_source") or "").strip().lower()
    return source in TRUSTED_EXECUTION_SOURCES


def sanitize_execution_values(values: dict[str, Any] | None) -> dict[str, Any]:
    """Remove legacy opt10046 aliases while preserving independent 5-minute strength."""

    result = deepcopy(values) if isinstance(values, dict) else {}
    if execution_source_trusted(result):
        return result
    for key in EXECUTION_FIELDS:
        result.pop(key, None)
    return result


def _install_rollover_source_guard() -> None:
    import realtime_v2.worker_approved_minute_rollover_guard as rollover

    original = getattr(rollover, "_approved_ui_fallback", None)
    if not callable(original) or getattr(
        original,
        "_stockboard_execution_source_guard_installed",
        False,
    ):
        return

    def trusted_fallback(source: dict[str, Any]) -> dict[str, Any]:
        return original(sanitize_execution_values(source))

    trusted_fallback._stockboard_execution_source_guard_installed = True
    rollover._approved_ui_fallback = trusted_fallback


def install(base) -> None:
    """Disable the legacy opt10046 execution alias and guard holiday restoration.

    Five-minute strength remains in its own strength_5m lane. Only values carrying the
    approved realtime 0B/FID228 source may enter the execution-strength lane. This adds
    no collector, QAx FID, REST request, WebSocket connection, thread or browser work.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_execution_strength_source_separation_installed",
        False,
    ):
        return

    _install_rollover_source_guard()
    original_init = state_class.__init__

    def state_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        status = getattr(self, "status", None)
        lock = getattr(self, "lock", None)
        values = {
            "execution_strength_alias_installed": False,
            "execution_strength_alias_policy": "disabled_opt10046_alias",
            "execution_strength_source_guard_installed": True,
            "execution_strength_source_guard_version": PATCH_VERSION,
            "execution_strength_trusted_sources": sorted(TRUSTED_EXECUTION_SOURCES),
        }
        if isinstance(status, dict) and lock is not None:
            with lock:
                status.update(values)
        elif isinstance(status, dict):
            status.update(values)

    state_class.__init__ = state_init
    state_class._stockboard_execution_strength_source_separation_installed = True
