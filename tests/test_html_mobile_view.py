from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import realtime_v2
from realtime_v2 import html_mobile_view_patch as mobile
from realtime_v2.html_momentum_badge_patch import install as install_momentum_html


ROOT = Path(__file__).resolve().parents[1]


def _render_mobile_chain(monkeypatch) -> str:
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(realtime_v2, "worker64_guarded_large", fake_large, raising=False)

    install_momentum_html()
    previous = fake_large._ui_safety_patch
    fast_patch = r'''
  function __sbv2FastPatchPriceRate(payload){
    document.querySelectorAll('table.board tbody tr[data-code]').forEach(tr => {
      const priceCell = tr.cells && tr.cells[4];
      const rateCell = tr.cells && tr.cells[5];
    });
  }
'''

    def with_fast_patch(html: str) -> str:
        rendered = previous(html)
        anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
        return rendered.replace(anchor, fast_patch + "\n" + anchor, 1)

    fake_large._ui_safety_patch = with_fast_patch
    mobile.install()
    source = (ROOT / "docs" / "stockboard_v2.html").read_text(encoding="utf-8-sig")
    return fake_large._ui_safety_patch(source)


def test_mobile_config_matches_approved_columns_and_widths():
    config = mobile._load_config()
    assert config["auto_mobile_max_width_px"] == 760
    assert config["mobile_columns"] == [
        "rank",
        "rank_change",
        "grade",
        "stock_name",
        "change_rate",
        "amount_ratio",
        "execution_strength",
        "program_net",
    ]
    assert sum(config["mobile_column_width_percent"].values()) == 100
    assert config["mobile_behavior"]["preserve_themeboard"] is True
    assert config["mobile_behavior"]["disable_hts_clipboard_link"] is True


def test_mobile_html_uses_eight_cells_and_auto_viewport_switch(monkeypatch):
    rendered = _render_mobile_chain(monkeypatch)

    assert mobile.MARKER in rendered
    assert 'id="stockboard-view-toggle"' in rendered
    assert "stockboardAutomaticMode" in rendered
    assert "auto_mobile_max_width_px" in rendered
    assert "window.addEventListener('resize'" in rendered
    assert "sessionStorage.removeItem(STOCKBOARD_VIEW_OVERRIDE_KEY)" in rendered
    assert "location.reload()" in rendered

    mobile_row = rendered.split("function mobileRowHtml(raw){", 1)[1].split(
        "function rowHtml(raw){", 1
    )[0]
    assert mobile_row.count("<td") == 8
    assert "r.price" not in mobile_row
    assert "r.trade_value_eok" not in mobile_row
    assert "r.daily_candle" not in mobile_row
    assert "r.bid_ask_ratio" not in mobile_row
    assert "r.strength_5m" not in mobile_row
    assert "r.large_trade_net_count" not in mobile_row

    assert "stockboardViewMode==='mobile' ? null" in rendered
    assert "stockboardViewMode==='mobile'?4:5" in rendered
    assert "if(!copyOnly&&stockboardViewMode!=='mobile')sendHtsCommand(text);" in rendered


def test_mobile_topbar_market_fit_and_themeboard_is_untouched(monkeypatch):
    rendered = _render_mobile_chain(monkeypatch)

    for hidden_id in (
        "#counts",
        "#latency",
        "#throughput",
        "#collector-metrics",
        "#worker-metrics",
        "#lag-metrics",
        "#render-metrics",
        "#metric-mode-status",
    ):
        assert hidden_id in rendered

    assert "strategyboard" in rendered.lower()
    assert "stockboard-mobile-strategy-hidden" in rendered
    assert "stockboardFitTableRight" in rendered
    assert "stockboardFitMarketContext" in rendered
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in rendered

    # ThemeBoard is intentionally not selected, hidden, renamed or linked by this patch.
    assert '[href*="themeboard"' not in rendered.lower()
    assert '[id*="themeboard"' not in rendered.lower()
    assert '[class*="themeboard"' not in rendered.lower()
    assert ".themeboard" not in rendered.lower()


def test_mobile_patch_adds_no_network_or_worker_data_path():
    source = inspect.getsource(mobile)
    assert "/api/" not in source
    assert "new EventSource" not in source
    assert "WebSocket(" not in source
    assert "Thread(" not in source
    assert "theme_projection" not in source
    assert "worker_theme" not in source


def test_mobile_patch_is_installed_after_momentum_html():
    source = (ROOT / "realtime_v2" / "worker_tr_singleflight_patch.py").read_text(
        encoding="utf-8"
    )
    assert source.index("install_momentum_badge_html()") < source.index(
        "install_mobile_view_html()"
    )
    assert re.search(
        r"from realtime_v2\.html_mobile_view_patch import \(\s*install as install_mobile_view_html",
        source,
    )
