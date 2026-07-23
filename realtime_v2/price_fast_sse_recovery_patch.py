from __future__ import annotations

"""Keep the browser price-only EventSource alive without touching market data paths.

The price delta stream is already lightweight, but browsers can leave a closed
EventSource object behind after a transient disconnect.  The existing UI keeps that
object in ``__sbv2PriceFastStream`` and relies only on native reconnect behavior.
When native reconnect stops, the board silently falls back to the slower full stream.

This patch adds an explicit 500 ms reconnect on ``onerror`` and reduces full scalar
resyncs from every 2 seconds to every 10 seconds.  QAx, FIDs, Collector, Worker quote
acceptance, delta calculation, ranking, trade value, and full-SSE cadence are unchanged.
"""

from typing import Any

PATCH_VERSION = "price_fast_sse_recovery_v1"
FULL_RESYNC_SEC = 10.0
RECONNECT_DELAY_MS = 500
_UI_MARKER = "STOCKBOARD_V2_PRICE_FAST_SSE_RECOVERY_20260723"


def _install_ui_recovery(large: Any) -> None:
    if large is None or getattr(large, "_stockboard_price_fast_sse_recovery_installed", False):
        return

    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if _UI_MARKER in patched:
            return patched

        declaration_anchor = "  let __sbv2FullStreamLagMs = null;\n"
        declaration_patch = (
            declaration_anchor
            + "  let __sbv2PriceFastReconnectTimer = null;\n"
            + f"  /* {_UI_MARKER} */\n"
        )
        if declaration_anchor in patched:
            patched = patched.replace(declaration_anchor, declaration_patch, 1)

        connect_anchor = (
            "    if(!window.EventSource || __sbv2PriceFastStream) return;\n"
            "    __sbv2PriceFastStream = new EventSource(`/api/v2/price-stream?limit=300&interval_ms=100&ts=${Date.now()}`);\n"
        )
        connect_patch = (
            "    if(!window.EventSource) return;\n"
            "    if(__sbv2PriceFastStream && __sbv2PriceFastStream.readyState !== EventSource.CLOSED) return;\n"
            "    if(__sbv2PriceFastReconnectTimer){ clearTimeout(__sbv2PriceFastReconnectTimer); __sbv2PriceFastReconnectTimer = null; }\n"
            "    __sbv2PriceFastStream = new EventSource(`/api/v2/price-stream?limit=300&interval_ms=100&ts=${Date.now()}`);\n"
        )
        if connect_anchor in patched:
            patched = patched.replace(connect_anchor, connect_patch, 1)

        error_anchor = (
            "    __sbv2PriceFastStream.onerror = () => {\n"
            "      /* EventSource reconnects automatically; the authoritative full stream remains active. */\n"
            "    };\n"
        )
        error_patch = (
            "    __sbv2PriceFastStream.onerror = () => {\n"
            "      const failed = __sbv2PriceFastStream;\n"
            "      __sbv2PriceFastStream = null;\n"
            "      try{ if(failed) failed.close(); }catch(_error){}\n"
            "      if(__sbv2PriceFastReconnectTimer) clearTimeout(__sbv2PriceFastReconnectTimer);\n"
            f"      __sbv2PriceFastReconnectTimer = setTimeout(__sbv2ConnectPriceFastStream, {RECONNECT_DELAY_MS});\n"
            "    };\n"
        )
        if error_anchor in patched:
            patched = patched.replace(error_anchor, error_patch, 1)

        return patched

    large._ui_safety_patch = patched_ui_safety_patch
    large._stockboard_price_fast_sse_recovery_installed = True


def install_runtime_wrapper() -> None:
    from realtime_v2 import price_fast_sse_patch as target

    if getattr(target, "_price_fast_sse_recovery_install_wrapped", False):
        return

    target.HEARTBEAT_SEC = FULL_RESYNC_SEC
    original_install = target.install

    def install_with_recovery(base, large=None) -> None:
        original_install(base, large)
        _install_ui_recovery(large)
        state_class = getattr(base, "State", None)
        if state_class is not None:
            state_class._stockboard_price_fast_sse_recovery_version = PATCH_VERSION

    target.install = install_with_recovery
    target._price_fast_sse_recovery_install_wrapped = True


__all__ = [
    "FULL_RESYNC_SEC",
    "PATCH_VERSION",
    "RECONNECT_DELAY_MS",
    "install_runtime_wrapper",
]
