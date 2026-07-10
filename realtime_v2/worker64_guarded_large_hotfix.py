from __future__ import annotations

import importlib

large = importlib.import_module("realtime_v2.worker64_guarded_large")
base = large.base

_original_speed_render_patch = large._speed_render_patch


def _large_hotfix_patch(html: str) -> str:
    html = _original_speed_render_patch(html)
    marker = "STOCKBOARD_V2_LARGE_UI_HOTFIX_20260710"
    if marker in html:
        return html

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r"""
  /* STOCKBOARD_V2_LARGE_UI_HOTFIX_20260710 */
  function __sbv2HotfixTypingTarget(target){
    const node = target && target.closest ? target.closest('input,textarea,select,[contenteditable="true"]') : null;
    return !!node;
  }

  function __sbv2HotfixRefreshMetricModes(){
    if(!lastPayload) return;
    try{
      const poolCache = typeof __largeSpeedRenderCache !== 'undefined' ? __largeSpeedRenderCache.get(poolBoardEl) : null;
      if(poolCache && poolCache.signatures){
        poolCache.signatures.clear();
        poolCache.cursor = 0;
      }
    }catch(_e){}
    for(let i = 0; i < 8; i += 1){
      setTimeout(() => {
        try{ render(lastPayload, 'stream', {forcePool:true}); }catch(_e){}
      }, i * 35);
    }
  }

  const __sbv2HotfixBaseToggleMetricMode = typeof toggleMetricMode === 'function' ? toggleMetricMode : null;
  toggleMetricMode = function(key){
    if(!metricKeys.has(key)) return;
    const current = metricModes[key] || 'number';
    const nextMode = current === 'bar' ? 'number' : 'bar';
    metricKeys.forEach(metricKey => {
      metricModes[metricKey] = nextMode;
    });
    saveMetricModes();
    updateMetricModeStatus();
    __sbv2HotfixRefreshMetricModes();
  };

  document.addEventListener('keydown', event => {
    if(event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    if(__sbv2HotfixTypingTarget(event.target)) return;
    const hasBoardRows = document.querySelector('table.board tbody tr[data-code]');
    if(!hasBoardRows) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    navigationActive = true;
    moveSelection(event.key === 'ArrowDown' ? 1 : -1);
  }, true);
"""
    if anchor not in html:
        return html
    return html.replace(anchor, f"{patch}\n{anchor}", 1)


large._speed_render_patch = _large_hotfix_patch


if __name__ == "__main__":
    raise SystemExit(base.main())
