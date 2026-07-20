from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import realtime_v2
from realtime_v2 import html_mobile_view_patch as mobile
from realtime_v2.html_momentum_badge_patch import install as install_momentum_html
from realtime_v2.worker_board_shell_patch import install as install_board_shell


ROOT = Path(__file__).resolve().parents[1]


def _render_mobile_chain(monkeypatch) -> str:
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(realtime_v2, "worker64_guarded_large", fake_large, raising=False)

    install_board_shell(fake_large)
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


def test_mobile_config_matches_approved_columns_controls_and_widths():
    config = mobile._load_config()
    assert config["schema_version"] == 4
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
    assert config["mobile_column_default_width_px"] == {
        "rank": 46,
        "rank_change": 52,
        "grade": 50,
        "stock_name": 132,
        "change_rate": 68,
        "amount_ratio": 62,
        "execution_strength": 70,
        "program_net": 68,
    }
    assert config["mobile_behavior"]["preserve_themeboard"] is True
    assert config["mobile_behavior"]["disable_hts_clipboard_link"] is False
    assert config["mobile_controls"] == {
        "preserve_ui_zoom": True,
        "preserve_column_minimize": True,
        "enable_column_resize": True,
        "enable_document_horizontal_scroll": True,
    }
    assert config["mobile_market"]["wrap_us_market"] is True
    assert config["mobile_market"]["show_all_domestic_columns"] is True
    assert config["mobile_market"]["market_graph_layout"] == "1x4"


def test_mobile_html_uses_eight_cells_auto_switch_and_ahk(monkeypatch):
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
    assert "numeric(r.price)" not in mobile_row
    assert "fmtNum(r.price" not in mobile_row
    assert "r.trade_value_eok" not in mobile_row
    assert "r.daily_candle" not in mobile_row
    assert "r.bid_ask_ratio" not in mobile_row
    assert "r.strength_5m" not in mobile_row
    assert "r.large_trade_net_count" not in mobile_row

    assert "stockboardViewMode==='mobile' ? null" in rendered
    assert "stockboardViewMode==='mobile'?4:5" in rendered
    assert "if(!copyOnly)sendHtsCommand(text);" in rendered
    assert "if(!copyOnly&&stockboardViewMode!=='mobile')" not in rendered


def test_mobile_restores_controls_resizers_and_independent_width_storage(monkeypatch):
    rendered = _render_mobile_chain(monkeypatch)

    assert "mobileColumnWidths:'stockboard.v2.mobileColumnWidths.v1'" in rendered
    assert "STORAGE_KEYS.mobileColumnWidths:STORAGE_KEYS.columnWidths" in rendered
    assert "mobile_column_default_width_px" in rendered
    assert "html.stockboard-mobile .board .column-resizer { display:block !important; }" in rendered
    assert "initColumnResizers()" in rendered
    assert "autoFitColumn(i,true)" in rendered
    assert "minimizeAllColumnWidths" in rendered

    hidden_block = rendered.split("html.stockboard-mobile #topbar .title,", 1)[1].split(
        "{ display:none !important; }", 1
    )[0]
    assert "#ui-zoom-toggle" not in hidden_block
    assert "#column-minimize-toggle" not in hidden_block
    assert "#row-position-toggle" in hidden_block


def test_mobile_market_wrap_fonts_scroll_and_empty_row_cleanup(monkeypatch):
    rendered = _render_mobile_chain(monkeypatch)

    assert "stockboardHideEmptyMetricRows" in rendered
    assert "stockboard-mobile-empty-row" in rendered
    assert "stockboardShowAllMarketColumns" in rendered
    assert "cell.hidden=false" in rendered
    assert "stockboardFitTableRight" not in rendered

    assert "html.stockboard-mobile .v2-us-grid tr" in rendered
    assert "flex-wrap:wrap" in rendered
    assert "html.stockboard-mobile .v2-us-grid td" in rendered
    assert "font-size:12px" in rendered
    assert "html.stockboard-mobile .v2-market-distribution" in rendered
    assert "flex-wrap:nowrap" in rendered
    assert "grid-template-columns:repeat(2" not in rendered
    assert "html.stockboard-mobile .v2-market-grid td" in rendered

    assert "overflow-x:auto !important" in rendered
    assert "width:var(--board-width) !important" in rendered
    assert "#sbv2-horizontal-scroll-spacer" in rendered
    assert "html.stockboard-mobile .board th { font-size:12px; }" in rendered


def test_mobile_strategy_hidden_and_themeboard_untouched(monkeypatch):
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

    assert '<a class="board-shell-tab" href="/theme">ThemeBoard</a>' in rendered
    assert '<span class="board-shell-tab disabled">StrategyBoard</span>' in rendered
    assert "#topbar .board-shell-tab.disabled" in rendered
    assert "#topbar .board-shell-new-window" in rendered
    assert "document.querySelectorAll('#topbar a,#topbar button,#topbar span')" in rendered
    assert "stockboard-mobile-strategy-hidden" in rendered

    patch_source = inspect.getsource(mobile).lower()
    assert '[href*="themeboard"' not in patch_source
    assert '[id*="themeboard"' not in patch_source
    assert '[class*="themeboard"' not in patch_source
    assert 'href="/theme"' not in patch_source


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
