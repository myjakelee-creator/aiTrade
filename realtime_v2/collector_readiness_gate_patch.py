from __future__ import annotations

from typing import Any

_MARKER = "stockboard_collector_readiness_gate_v1"


def _positive_int(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def install(base) -> None:
    """Publish collector readiness only after login and SetRealReg actually succeed.

    The launcher waits for ``provider_started`` and a positive ``registered_count``.
    ``provider.register_codes`` returns the requested count even while login is still
    pending, so reporting that value directly can make the launcher terminate
    opstarter before OnEventConnect arrives.  Keep the requested count for
    diagnostics, but expose ``registered_count`` only when the provider reports both
    a connected login and successful real-time registration.
    """

    if getattr(base, _MARKER, False):
        return

    original_publish = base.publish_collector_status

    def publish_collector_status(sender, provider, extra=None) -> None:
        next_extra = dict(extra or {})
        requested_count = _positive_int(next_extra.get("registered_count"))

        try:
            provider_status = provider.status()
        except Exception:
            provider_status = {}

        login_connected = str(provider_status.get("login_state") or "").lower() == "connected"
        realreg_succeeded = provider_status.get("realreg_succeeded") is True
        actual_count = _positive_int(provider_status.get("realreg_code_count"))
        collector_ready = login_connected and realreg_succeeded and actual_count > 0

        next_extra["registered_count_requested"] = requested_count
        next_extra["registered_count"] = actual_count if collector_ready else 0
        next_extra["collector_ready"] = collector_ready
        next_extra["collector_ready_reason"] = (
            "login_connected_and_realreg_succeeded"
            if collector_ready
            else "waiting_login"
            if not login_connected
            else "waiting_realreg"
        )

        original_publish(sender, provider, next_extra)

    base.publish_collector_status = publish_collector_status
    setattr(base, _MARKER, True)
