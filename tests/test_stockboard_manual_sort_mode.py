from __future__ import annotations

import ast
from pathlib import Path

from realtime_v2.stockboard_manual_sort_mode_patch import (
    MARKER,
    apply_manual_sort_mode,
)


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "stockboard_manual_sort_mode_patch.py"
ANCHOR = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"


def source_html() -> str:
    return """
<script>
let sortState={key:'grade_score',dir:'desc'};
function saveSortState(){}
function markSortHeaders(){}
function render(){}
let lastPayload={};
function __sbv2ClientSortActive(){return true;}
const latencyEl={textContent:''};
const mode='stream',lag=1;
latencyEl.textContent=`${mode} ${lag===null?'-':lag.toFixed(0)} ms · sort ${sortState.key}/${sortState.dir}`;
document.addEventListener('click',e=>{ const th=e.target.closest('th[data-sort-key]'); if(!th||e.target.closest('.column-resizer'))return; const key=th.dataset.sortKey; window.__sbv2HeaderSortActive=true; try{localStorage.setItem('stockboard.v2.headerSortActive.v1','1');}catch(_e){} sortState={key,dir:sortState.key===key&&sortState.dir==='asc'?'desc':'asc'}; saveSortState(); markSortHeaders(); if(lastPayload)render(lastPayload,'stream',{forcePool:true}); }, true);
""" + ANCHOR + "\n</script>"


def test_patch_is_valid_python_and_adds_no_transport_work():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in ("dynamicCall", "QAxWidget", "requests.", "socket", "EventSource", "fetch("):
        assert forbidden not in source


def test_manual_sort_cycles_sort_reverse_then_auto():
    patched = apply_manual_sort_mode(source_html())

    assert MARKER in patched
    assert "__sbv2DefaultManualSortDir" in patched
    assert "__sbv2SetManualSortActive(false)" in patched
    assert "__sbv2ManualSortActive()?`${sortState.key}/${sortState.dir}`:'auto'" in patched
    assert "sort ${__sbv2SortModeLabel()}" in patched


def test_manual_sort_is_browser_view_only():
    patched = apply_manual_sort_mode(source_html())

    assert "worker HOT/WARM/COLD" in patched
    assert "__sbv2ClientSortActive=function(){return __sbv2ManualSortActive();};" in patched
    assert "/api/" not in patched
    assert "candidate_score" not in patched
    assert "active_lane" not in patched


def test_patch_is_idempotent():
    once = apply_manual_sort_mode(source_html())
    twice = apply_manual_sort_mode(once)
    assert once == twice
    assert once.count(MARKER) == 1
