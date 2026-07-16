from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import realtime_v2
from realtime_v2.html_approved_minute_metrics_patch import install as install_minute_html
from realtime_v2.html_large_trade_quality_patch import install as install_large_quality_html
from realtime_v2.html_opening_render_guard_patch import install as install_opening_guard_html


ROOT = Path(__file__).resolve().parents[1]


def test_actual_stockboard_html_accepts_complete_momentum_patch_chain(monkeypatch):
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(realtime_v2, "worker64_guarded_large", fake_large, raising=False)

    install_minute_html()
    install_large_quality_html()
    install_opening_guard_html()

    source = (ROOT / "docs" / "stockboard_v2.html").read_text(encoding="utf-8-sig")
    rendered = fake_large._ui_safety_patch(source)

    assert "STOCKBOARD_V2_APPROVED_MINUTE_METRICS_20260716" in rendered
    assert "STOCKBOARD_V2_MOMENTUM_BADGES_20260716" in rendered
    assert "STOCKBOARD_V2_LARGE_TRADE_QUALITY_20260716" in rendered
    assert "STOCKBOARD_V2_REALTIME_CANDLE_CLOSE_20260716" in rendered
    assert "label:'모멘텀'" in rendered
    assert "momentumHtml(r)" in rendered
    assert "badges.slice(0,2)" in rendered
    assert "close=numeric(r.price??r.trade_price??o?.current??o?.close??r.close)" in rendered
