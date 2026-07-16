from __future__ import annotations

MARKER = "STOCKBOARD_V2_REALTIME_CANDLE_CLOSE_20260716"


def install() -> None:
    """Render the daily candle close from the latest realtime row price.

    Open, high and low continue to come from the existing OHLC payload. Only the
    displayed candle close changes its source priority to row.price/trade_price so the
    candle body follows the same realtime value shown in the current-price column.
    This is a browser-side value selection change only; it adds no network, QAx, worker,
    timer or render-frequency work.
    """

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_realtime_candle_close_html_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    old = (
        "close=numeric(o?.close??o?.current??r.close??r.price??r.trade_price);"
    )
    new = (
        "close=numeric(r.price??r.trade_price??o?.current??o?.close??r.close);"
    )

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched
        if old not in patched:
            raise RuntimeError("daily candle close expression not found")
        patched = patched.replace(old, new, 1)
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._realtime_candle_close_html_installed = True
