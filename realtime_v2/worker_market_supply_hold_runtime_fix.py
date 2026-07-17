from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.worker_market_supply_hold_patch import MarketSupplyHold

PATCH_VERSION = "market_supply_hold_runtime_fix_v1"


def install() -> None:
    """Attach market-supply hold to the actual /api/v2/context owner module."""

    from realtime_v2 import worker64_guarded as guarded

    if getattr(guarded, "_market_supply_hold_patch_installed", False):
        return

    original_context = getattr(guarded, "_runtime_context_payload", None)
    if not callable(original_context):
        raise AttributeError(
            "realtime_v2.worker64_guarded._runtime_context_payload is unavailable"
        )

    holder = MarketSupplyHold()

    def patched_runtime_context_payload() -> dict[str, Any]:
        payload = original_context()
        payload = dict(payload) if isinstance(payload, dict) else {}
        market_supply, status = holder.resolve(payload.get("market_supply"))
        status = dict(status) if isinstance(status, dict) else {}
        status["runtime_fix_version"] = PATCH_VERSION
        status["context_owner"] = "realtime_v2.worker64_guarded"
        payload["market_supply"] = deepcopy(market_supply)
        payload["market_supply_status"] = status
        return payload

    guarded._runtime_context_payload = patched_runtime_context_payload
    guarded._market_supply_hold = holder
    guarded._market_supply_hold_patch_installed = True
    guarded._market_supply_hold_patch_owner = "realtime_v2.worker64_guarded"
