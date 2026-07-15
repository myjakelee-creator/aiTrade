from __future__ import annotations

import re


MARKER = "STOCKBOARD_V2_MANUAL_SORT_MODE_V2_20260714"
MANUAL_SORT_KEY = "stockboard.v2.manualSortMode.v2"
LEGACY_SORT_KEY = "stockboard.v2.headerSortActive.v1"

_CLICK_LISTENER = re.compile(
    r"document\.addEventListener\('click',e=>\{\s*"
    r"const th=e\.target\.closest\('th\[data-sort-key\]'\);"
    r".*?\}, true\);",
    re.DOTALL,
)

_REPLACEMENT_LISTENER = r"""document.addEventListener('click',e=>{
    const th=e.target.closest('th[data-sort-key]');
    if(!th||e.target.closest('.column-resizer'))return;
    const key=th.dataset.sortKey;
    const defaultDir=__sbv2DefaultManualSortDir(key);
    if(!__sbv2ManualSortActive() || sortState.key!==key){
      sortState={key,dir:defaultDir};
      __sbv2SetManualSortActive(true);
    }else if(sortState.dir===defaultDir){
      sortState={key,dir:defaultDir==='asc'?'desc':'asc'};
    }else{
      __sbv2SetManualSortActive(false);
    }
    saveSortState();
    markSortHeaders();
    if(lastPayload)render(lastPayload,'stream',{forcePool:true});
  }, true);"""


def apply_manual_sort_mode(html: str) -> str:
    if MARKER in html:
        return html

    replaced, count = _CLICK_LISTENER.subn(_REPLACEMENT_LISTENER, html, count=1)
    if count != 1:
        return html

    replaced = replaced.replace(
        "latencyEl.textContent=`${mode} ${lag===null?'-':lag.toFixed(0)} ms · sort ${sortState.key}/${sortState.dir}`;",
        "latencyEl.textContent=`${mode} ${lag===null?'-':lag.toFixed(0)} ms · sort ${__sbv2SortModeLabel()}`;",
        1,
    )

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __MARKER__ */
  const __sbv2ManualSortStorageKey='__MANUAL_KEY__';
  try{localStorage.removeItem('__LEGACY_KEY__');}catch(_e){}

  function __sbv2ManualSortActive(){
    try{return localStorage.getItem(__sbv2ManualSortStorageKey)==='1';}
    catch(_e){return false;}
  }
  function __sbv2SetManualSortActive(active){
    window.__sbv2HeaderSortActive=!!active;
    try{
      if(active)localStorage.setItem(__sbv2ManualSortStorageKey,'1');
      else localStorage.removeItem(__sbv2ManualSortStorageKey);
    }catch(_e){}
  }
  function __sbv2DefaultManualSortDir(key){
    return key==='rank'||key==='stock_name'?'asc':'desc';
  }
  function __sbv2SortModeLabel(){
    return __sbv2ManualSortActive()?`${sortState.key}/${sortState.dir}`:'auto';
  }

  // Manual sorting changes only the browser view. The worker HOT/WARM/COLD
  // membership and stable slots remain untouched.
  __sbv2ClientSortActive=function(){return __sbv2ManualSortActive();};

  const __sbv2OriginalManualMarkSortHeaders=markSortHeaders;
  markSortHeaders=function(){
    if(!__sbv2ManualSortActive()){
      document.querySelectorAll('th[data-sort-key]').forEach(th=>delete th.dataset.sortDir);
      return;
    }
    return __sbv2OriginalManualMarkSortHeaders();
  };
'''.replace("__MARKER__", MARKER).replace("__MANUAL_KEY__", MANUAL_SORT_KEY).replace("__LEGACY_KEY__", LEGACY_SORT_KEY)

    if anchor not in replaced:
        return html
    return replaced.replace(anchor, f"{patch}\n{anchor}", 1)


def install() -> None:
    from realtime_v2 import html_header_sort_patch as header_sort

    if getattr(header_sort, "_stockboard_manual_sort_mode_installed", False):
        return

    original_apply = header_sort.apply_header_sort_patch

    def patched_apply_header_sort(html: str) -> str:
        return apply_manual_sort_mode(original_apply(html))

    header_sort.apply_header_sort_patch = patched_apply_header_sort
    header_sort._stockboard_manual_sort_mode_installed = True
