from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

large = importlib.import_module("realtime_v2.worker64_guarded_large")
base = large.base

_CROSS_TABLE_NAV_MARKER = "STOCKBOARD_V2_CROSS_TABLE_NAV_20260710"
_CROSS_TABLE_NAV_ANCHOR = (
    "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});"
    "loadCandidateModels();loadContext();markSortHeaders();connectStream();"
)
_CROSS_TABLE_NAV_PATCH = r"""
  /* STOCKBOARD_V2_CROSS_TABLE_NAV_20260710 */
  function __sbv2CrossTableNavRows(){
    const rows = [];
    const seen = new Set();
    [focusBoardEl, poolBoardEl].forEach(table => {
      const body = table && table.tBodies ? table.tBodies[0] : null;
      if(!body) return;
      body.querySelectorAll('tr[data-code]').forEach(row => {
        const code = String(row.dataset.code || '');
        if(!/^\d{6}$/.test(code) || seen.has(code)) return;
        seen.add(code);
        rows.push({row, table, code});
      });
    });
    return rows;
  }

  function __sbv2MoveAcrossBoards(delta){
    const rows = __sbv2CrossTableNavRows();
    if(!rows.length) return false;
    const active = document.activeElement;
    let index = rows.findIndex(item => item.row === active || item.code === selectedCode);
    if(index < 0) index = delta > 0 ? -1 : 0;
    const next = rows[(index + delta + rows.length) % rows.length];
    if(!next || !/^\d{6}$/.test(next.code)) return false;

    if(typeof __sbv2LastNavTable !== 'undefined') __sbv2LastNavTable = next.table;
    if(typeof __sbv2RefocusVisibleRow === 'function'){
      __sbv2RefocusVisibleRow(next.table, next.code);
    }else if(next.row && typeof next.row.focus === 'function'){
      next.row.focus({preventScroll:true});
      if(typeof next.row.scrollIntoView === 'function'){
        next.row.scrollIntoView({block:'nearest', inline:'nearest'});
      }
    }
    selectCodeAndLink(next.code, false, {focus:false});
    return true;
  }

  if(typeof moveSelection === 'function'){
    const __sbv2OriginalCrossTableMoveSelection = moveSelection;
    moveSelection = function(delta){
      if(__sbv2MoveAcrossBoards(delta)) return;
      return __sbv2OriginalCrossTableMoveSelection(delta);
    };
  }
"""

_DISPLAY50_MARKER = "STOCKBOARD_V2_DISPLAY50_FAST_PRICE_20260713"
_DISPLAY50_PATCH = r"""
  /* STOCKBOARD_V2_DISPLAY50_FAST_PRICE_20260713 */
  const __sbv2HeavyRenderIntervalMs = 500;
  let __sbv2LastHeavyRenderAt = 0;

  function __sbv2FastPatchPriceRate(payload){
    const rows = Array.isArray(payload && payload.rows) ? payload.rows : [];
    if(!rows.length) return;
    const byCode = new Map(rows.map(row => [String(row && row.stock_code || ''), row]));

    document.querySelectorAll('table.board tbody tr[data-code]').forEach(tr => {
      const row = byCode.get(String(tr.dataset.code || ''));
      if(!row) return;

      const price = numeric(row.price);
      const rate = numeric(row.change_rate);
      const priceCell = tr.cells && tr.cells[4];
      const rateCell = tr.cells && tr.cells[5];

      if(priceCell){
        const nextText = price === null ? '-' : fmtNum(price);
        if(priceCell.textContent !== nextText) priceCell.textContent = nextText;
      }

      if(rateCell){
        const nextText = rate === null ? '-' : `${rate > 0 ? '+' : ''}${rate.toFixed(2)}%`;
        if(rateCell.textContent !== nextText) rateCell.textContent = nextText;
        rateCell.classList.remove('plus', 'minus', 'zero');
        rateCell.classList.add(rate === null || rate === 0 ? 'zero' : rate > 0 ? 'plus' : 'minus');
      }
    });
  }

  function __sbv2HandleFastSnapshot(payload){
    const now = performance.now();
    const first = !lastPayload;
    lastPayload = payload;
    __sbv2FastPatchPriceRate(payload);
    if(first || now - __sbv2LastHeavyRenderAt >= __sbv2HeavyRenderIntervalMs){
      __sbv2LastHeavyRenderAt = now;
      render(payload, 'stream');
    }
  }
"""


def _runtime_dir() -> Path:
    try:
        return Path(base.RUNTIME_DIR)
    except Exception:
        return ROOT / "data" / "runtime" / "stockboard_v2"


def _write_patch_error(filename: str, error: Exception) -> None:
    try:
        runtime = _runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / filename).write_text(
            f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass


def _install_bidask_patch_fail_open() -> None:
    try:
        from realtime_v2.bidask_last_cache_patch import install as install_bidask_last_cache

        install_bidask_last_cache(base)
    except Exception as error:
        _write_patch_error("bidask_worker_patch_error.txt", error)


def _install_display_hold_fail_open() -> None:
    try:
        from realtime_v2.display_hold_policy_patch import install as install_display_hold

        install_display_hold(base)
    except Exception as error:
        _write_patch_error("display_hold_patch_error.txt", error)


def _install_display_hold_ohlc_price_fail_open() -> None:
    try:
        from realtime_v2.display_hold_ohlc_price_patch import install as install_ohlc_price_hold

        install_ohlc_price_hold(base)
    except Exception as error:
        _write_patch_error("display_hold_ohlc_price_patch_error.txt", error)


def _install_session_metric_hold_fail_open() -> None:
    try:
        from realtime_v2.session_metric_hold_patch import install as install_session_metric_hold

        install_session_metric_hold(base)
    except Exception as error:
        _write_patch_error("session_metric_hold_patch_error.txt", error)


def _install_execution_strength_alias_fail_open() -> None:
    try:
        from realtime_v2.execution_strength_alias_patch import install as install_execution_strength_alias

        install_execution_strength_alias(base)
    except Exception as error:
        _write_patch_error("execution_strength_alias_patch_error.txt", error)


def _install_event_freshness_fail_open() -> None:
    try:
        from realtime_v2.worker_event_freshness_patch import install as install_event_freshness

        install_event_freshness(base)
    except Exception as error:
        _write_patch_error("worker_event_freshness_patch_error.txt", error)


def _install_cross_table_navigation_patch_fail_open() -> None:
    try:
        if getattr(large, "_cross_table_navigation_patch_installed", False):
            return

        original_ui_safety_patch = large._ui_safety_patch

        def patched_ui_safety_patch(html: str) -> str:
            patched = original_ui_safety_patch(html)
            if _CROSS_TABLE_NAV_MARKER in patched or _CROSS_TABLE_NAV_ANCHOR not in patched:
                return patched
            return patched.replace(
                _CROSS_TABLE_NAV_ANCHOR,
                f"{_CROSS_TABLE_NAV_PATCH}\n{_CROSS_TABLE_NAV_ANCHOR}",
                1,
            )

        large._ui_safety_patch = patched_ui_safety_patch
        large._cross_table_navigation_patch_installed = True
    except Exception as error:
        _write_patch_error("cross_table_navigation_patch_error.txt", error)


def _install_header_sort_patch_fail_open() -> None:
    try:
        from realtime_v2.html_header_sort_patch import install as install_header_sort

        install_header_sort(base, large)
    except Exception as error:
        _write_patch_error("html_header_sort_patch_error.txt", error)


def _install_display50_fast_price_patch_fail_open() -> None:
    try:
        if getattr(large, "_display50_fast_price_patch_installed", False):
            return

        original_ui_safety_patch = large._ui_safety_patch

        def patched_ui_safety_patch(html: str) -> str:
            patched = original_ui_safety_patch(html)
            if _DISPLAY50_MARKER in patched:
                return patched

            patched = patched.replace(
                "/api/v2/snapshot?limit=300&ts=${Date.now()}",
                "/api/v2/snapshot?limit=50&ts=${Date.now()}",
            )
            patched = patched.replace(
                "/api/v2/stream?limit=300&interval_ms=100&ts=${Date.now()}",
                "/api/v2/stream?limit=50&interval_ms=100&ts=${Date.now()}",
            )
            patched = patched.replace("Top300 Pool", "표시 Pool")
            patched = patched.replace(
                "return minutes >= 9 * 60 && minutes < 9 * 60 + 10 ? 1000 : 250;",
                "return 1000;",
            )
            patched = patched.replace(
                "countsEl.textContent=`rows ${payload.row_count||raw.length}",
                "countsEl.textContent=`표시 ${payload.row_count||raw.length} / 내부 ${payload.status?.universe_count||raw.length}",
            )
            patched = patched.replace(
                "stream.addEventListener('snapshot',e=>{try{lastStreamAt=performance.now();render(JSON.parse(e.data),'stream');}catch(err){console.warn(err);}});",
                "stream.addEventListener('snapshot',e=>{try{lastStreamAt=performance.now();__sbv2HandleFastSnapshot(JSON.parse(e.data));}catch(err){console.warn(err);}});",
            )

            if _CROSS_TABLE_NAV_ANCHOR in patched:
                patched = patched.replace(
                    _CROSS_TABLE_NAV_ANCHOR,
                    f"{_DISPLAY50_PATCH}\n{_CROSS_TABLE_NAV_ANCHOR}",
                    1,
                )
            return patched

        large._ui_safety_patch = patched_ui_safety_patch
        large._display50_fast_price_patch_installed = True
    except Exception as error:
        _write_patch_error("display50_fast_price_patch_error.txt", error)


def _install_opening_burst_cache_fail_open() -> None:
    try:
        from realtime_v2.worker_opening_burst_cache_patch import (
            install as install_opening_burst_cache,
        )

        install_opening_burst_cache(base)
    except Exception as error:
        _write_patch_error("opening_burst_cache_patch_error.txt", error)


_install_bidask_patch_fail_open()
_install_display_hold_fail_open()
_install_display_hold_ohlc_price_fail_open()
_install_session_metric_hold_fail_open()
_install_execution_strength_alias_fail_open()
_install_event_freshness_fail_open()
_install_cross_table_navigation_patch_fail_open()
_install_header_sort_patch_fail_open()
_install_display50_fast_price_patch_fail_open()
# Install last so it caches the final ranking/display/hold snapshot implementation.
_install_opening_burst_cache_fail_open()

if __name__ == "__main__":
    raise SystemExit(base.main())
