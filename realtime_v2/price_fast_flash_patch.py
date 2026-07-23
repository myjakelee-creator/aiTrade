from __future__ import annotations

"""Restore the existing yellow price flash on the lightweight price SSE path.

The price-only EventSource updates visible price and change-rate cells directly through
``__sbv2FastPatchPriceRate``.  That deliberately avoids the expensive table render, but
it also bypasses ``cellFlashClass`` and therefore the existing yellow flash animation.

This patch wraps only the browser DOM updater.  It does not add a stream, timer, network
request, market-data calculation, Worker mutation, QAx/FID registration, or render loop.
The existing flash scope (S1, focus rows, and pool ranks 21-50) remains authoritative.
"""

from typing import Any

PATCH_VERSION = "price_fast_flash_v1"
_UI_MARKER = "STOCKBOARD_V2_PRICE_FAST_FLASH_20260723"
_UI_ANCHOR = (
    "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});"
    "loadCandidateModels();loadContext();markSortHeaders();connectStream();"
)

_UI_PATCH = r"""
  /* STOCKBOARD_V2_PRICE_FAST_FLASH_20260723 */
  const __sbv2FastFlashValues = new Map();

  function __sbv2FastFlashAllowed(code){
    try{
      if(typeof __sbv2FlashScopeState !== 'undefined' && __sbv2FlashScopeState?.map instanceof Map){
        return __sbv2FlashScopeState.map.get(code) === true;
      }
    }catch(_error){}
    return true;
  }

  function __sbv2PlayFastCellFlash(cell){
    if(!cell || typeof cell.animate !== 'function') return;
    cell.animate(
      [
        {boxShadow:'inset 0 0 0 9999px rgba(250, 204, 21, .46)'},
        {boxShadow:'inset 0 0 0 9999px rgba(250, 204, 21, 0)'}
      ],
      {duration:620, easing:'ease-out', iterations:1}
    );
  }

  function __sbv2FastFlashChanged(rows){
    const changed = [];
    (Array.isArray(rows) ? rows : []).forEach(update => {
      const code = String(update && update.stock_code || '');
      if(!/^\d{6}$/.test(code)) return;
      [['price',5],['change_rate',6]].forEach(([key,column]) => {
        const value = String(update && update[key] !== undefined && update[key] !== null ? update[key] : '');
        const cacheKey = `${code}|${key}`;
        const previous = __sbv2FastFlashValues.get(cacheKey);
        __sbv2FastFlashValues.set(cacheKey, value);
        if(previous !== undefined && previous !== value) changed.push({code,column});
      });
    });
    return changed;
  }

  if(typeof __sbv2FastPatchPriceRate === 'function'){
    const __sbv2FastFlashOriginalPatch = __sbv2FastPatchPriceRate;
    __sbv2FastPatchPriceRate = function(payload){
      const changed = __sbv2FastFlashChanged(payload && payload.rows);
      const result = __sbv2FastFlashOriginalPatch.apply(this, arguments);
      changed.forEach(item => {
        if(!__sbv2FastFlashAllowed(item.code)) return;
        document.querySelectorAll(`table.board tbody tr[data-code="${item.code}"] td:nth-child(${item.column})`)
          .forEach(__sbv2PlayFastCellFlash);
      });
      return result;
    };
  }
"""


def _install_ui_flash(large: Any) -> None:
    if large is None or getattr(large, "_stockboard_price_fast_flash_installed", False):
        return

    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if _UI_MARKER in patched or _UI_ANCHOR not in patched:
            return patched
        return patched.replace(_UI_ANCHOR, f"{_UI_PATCH}\n{_UI_ANCHOR}", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._stockboard_price_fast_flash_installed = True


def install_runtime_wrapper() -> None:
    from realtime_v2 import price_fast_sse_patch as target

    if getattr(target, "_price_fast_flash_install_wrapped", False):
        return

    original_install = target.install

    def install_with_flash(base, large=None) -> None:
        original_install(base, large)
        _install_ui_flash(large)
        state_class = getattr(base, "State", None)
        if state_class is not None:
            state_class._stockboard_price_fast_flash_version = PATCH_VERSION

    target.install = install_with_flash
    target._price_fast_flash_install_wrapped = True


__all__ = ["PATCH_VERSION", "install_runtime_wrapper"]
