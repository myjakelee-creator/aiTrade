from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import realtime_v2
from realtime_v2 import html_mobile_top_status_patch as top_status
from realtime_v2 import html_mobile_view_patch as mobile
from realtime_v2.html_momentum_badge_patch import install as install_momentum_html
from realtime_v2.worker_board_shell_patch import install as install_board_shell


ROOT = Path(__file__).resolve().parents[1]


def _render_chain(monkeypatch) -> str:
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
    top_status.install()
    source = (ROOT / "docs" / "stockboard_v2.html").read_text(encoding="utf-8-sig")
    return fake_large._ui_safety_patch(source)


def test_mobile_top_status_config_is_approved():
    config = top_status._load_config()
    assert config == {
        "enabled": True,
        "throughput_metric": "recv_s",
        "show_stream_ms": True,
        "show_render_ms": True,
        "position": "momentum_top_row",
    }


def test_mobile_top_status_wraps_momentum_and_updates_existing_metrics(monkeypatch):
    rendered = _render_chain(monkeypatch)

    assert top_status.MARKER in rendered
    assert 'id="stockboard-mobile-top-status-row"' in rendered
    assert 'id="stockboard-mobile-performance"' in rendered
    assert "recv -/s · stream -ms · render -ms" in rendered
    assert "stockboardUpdateMobilePerformance(rates,lag,ms,mode);" in rendered
    assert "parts.push(`recv ${recv}/s`)" in rendered
    assert "parts.push(`${mode==='stream'?'stream':'poll'} ${stream}ms`)" in rendered
    assert "parts.push(`render ${render}ms`)" in rendered


def test_mobile_top_status_is_first_row_and_preserves_desktop(monkeypatch):
    rendered = _render_chain(monkeypatch)

    assert "#stockboard-mobile-top-status-row { display:contents; }" in rendered
    assert "html.stockboard-mobile #stockboard-mobile-top-status-row" in rendered
    assert "order:-10;" in rendered
    assert "#stockboard-mobile-top-status-row #momentum-alert-strip" in rendered
    assert "order:0 !important;" in rendered
    assert "flex:1 1 auto;" in rendered
    assert "#stockboard-mobile-performance { display:none; }" in rendered
    assert "html.stockboard-mobile #stockboard-mobile-performance" in rendered


def test_mobile_top_status_adds_no_new_data_or_timer_path():
    source = inspect.getsource(top_status)
    assert "/api/" not in source
    assert "new EventSource" not in source
    assert "WebSocket(" not in source
    assert "Thread(" not in source
    assert "MutationObserver" not in source
    assert "setInterval(" not in source
    assert "theme_projection" not in source
    assert "worker_theme" not in source


def test_mobile_top_status_is_installed_after_mobile_view():
    source = (ROOT / "realtime_v2" / "worker_tr_singleflight_patch.py").read_text(
        encoding="utf-8"
    )
    assert source.index("install_mobile_view_html()") < source.index(
        "install_mobile_top_status_html()"
    )
    assert "from realtime_v2.html_mobile_top_status_patch import" in source
