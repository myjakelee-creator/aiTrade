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
        "position": "after_candidate_selector",
        "display_mode": "separate_badges",
        "wrap": True,
    }


def test_mobile_momentum_is_full_width_and_speed_badges_follow_selector(monkeypatch):
    rendered = _render_chain(monkeypatch)

    assert top_status.MARKER in rendered
    assert 'id="stockboard-mobile-top-status-row"' not in rendered
    assert 'id="stockboard-mobile-performance"' not in rendered
    assert 'id="stockboard-mobile-recv"' in rendered
    assert 'id="stockboard-mobile-stream"' in rendered
    assert 'id="stockboard-mobile-render"' in rendered

    selector_pos = rendered.index('id="candidate-model-selector"')
    recv_pos = rendered.index('id="stockboard-mobile-recv"')
    stream_pos = rendered.index('id="stockboard-mobile-stream"')
    render_pos = rendered.index('id="stockboard-mobile-render"')
    copy_pos = rendered.index('id="copy-status"')
    assert selector_pos < recv_pos < stream_pos < render_pos < copy_pos

    assert "html.stockboard-mobile #momentum-alert-strip" in rendered
    assert "order:-20 !important;" in rendered
    assert "flex:0 0 100% !important;" in rendered
    assert "width:100% !important;" in rendered
    assert "max-width:100% !important;" in rendered


def test_mobile_topbar_scrolls_vertically_and_keeps_horizontal_anchor(monkeypatch):
    rendered = _render_chain(monkeypatch)

    mobile_override = rendered.split(
        "html.stockboard-mobile #topbar.topbar {", 1
    )[1].split("}", 1)[0]
    assert "top:auto !important;" in mobile_override
    assert "inset-block-start:auto !important;" in mobile_override
    assert "position:static" not in mobile_override
    assert "position:fixed" not in mobile_override

    assert (
        "html.stockboard-mobile #topbar { position:sticky; left:0; width:100vw;"
        in rendered
    )
    assert "#topbar.topbar {" in rendered
    assert "top: 0 !important;" in rendered


def test_mobile_speed_badges_reuse_existing_values_and_wrap(monkeypatch):
    rendered = _render_chain(monkeypatch)

    assert "stockboardUpdateMobilePerformance(rates,lag,ms,mode);" in rendered
    assert "stockboardMobileRecvEl.textContent=`recv/s ${recv}`" in rendered
    assert "stockboardMobileStreamEl.textContent=`${mode==='stream'?'stream':'poll'} ${stream} ms`" in rendered
    assert "stockboardMobileRenderEl.textContent=`render ${render} ms`" in rendered

    assert ".stockboard-mobile-performance-badge { display:none; }" in rendered
    assert "html.stockboard-mobile .stockboard-mobile-performance-badge" in rendered
    assert "font-size:inherit;" in rendered
    assert "font-weight:inherit;" in rendered
    assert "flex:0 0 auto;" in rendered
    assert "flex-wrap: wrap !important;" in rendered


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
