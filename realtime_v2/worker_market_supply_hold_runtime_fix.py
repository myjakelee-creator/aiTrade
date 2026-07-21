from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from realtime_v2.worker_market_supply_hold_patch import MarketSupplyHold

PATCH_VERSION = "market_supply_hold_runtime_fix_v1"
PORTABLE_PARSER_VERSION = "exact_daily_row_fields_v2"


def _record_optional_error(base, filename: str, error: Exception) -> None:
    try:
        runtime = Path(getattr(base, "RUNTIME_DIR"))
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / filename).write_text(
            f"{type(error).__name__}: {error}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def install() -> None:
    """Attach runtime context, board continuity, and momentum protections."""

    from realtime_v2 import worker64_guarded as guarded
    from realtime_v2 import worker_board_trading_date_guard as board_guard
    from realtime_v2.worker_board_display_continuity_patch import (
        install as install_board_display_continuity,
    )
    from realtime_v2.worker_board_display_continuity_safety import (
        install as install_board_display_continuity_safety,
    )
    from realtime_v2.worker_board_display_continuity_runtime_opt import (
        install as install_board_display_continuity_runtime_opt,
    )
    from realtime_v2.worker_portable_rebuild_status_patch import (
        install as install_portable_rebuild_status,
    )

    board_guard.PORTABLE_PARSER_VERSION = PORTABLE_PARSER_VERSION

    guard_base = getattr(guarded, "base", None)
    if guard_base is not None:
        board_guard.install(guard_base)
        install_board_display_continuity(guard_base)
        install_board_display_continuity_safety(guard_base)
        install_board_display_continuity_runtime_opt(guard_base)
        install_portable_rebuild_status()
        try:
            from realtime_v2.worker_momentum_accuracy_stage_bridge import (
                install as install_momentum_accuracy_stage,
            )

            install_momentum_accuracy_stage(guard_base)
            try:
                (Path(guard_base.RUNTIME_DIR) / "momentum_accuracy_patch_error.txt").unlink(
                    missing_ok=True
                )
            except Exception:
                pass
        except Exception as error:
            _record_optional_error(
                guard_base,
                "momentum_accuracy_patch_error.txt",
                error,
            )

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
