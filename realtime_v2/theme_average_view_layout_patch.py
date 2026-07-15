from __future__ import annotations

import time
from importlib import import_module
from typing import Any


MARKER = "THEMEBOARD_AVERAGE_VIEW_RESIZABLE_COLUMNS_20260714"


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _install_average_rows(theme_module) -> None:
    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_average_theme_view_installed", False):
        return

    original_call = builder_class.__call__

    def call(self, feature_version, rows, meta):
        started = time.perf_counter()
        payload = original_call(self, feature_version, rows, meta)
        if not isinstance(payload, dict) or payload.get("status") != "READY":
            return payload

        source_rows = [
            row for row in payload.get("rows") or [] if isinstance(row, dict)
        ]
        average_rows = sorted(
            source_rows,
            key=lambda row: (
                _number(row.get("avg_change_rate")) is None,
                -float(
                    _number(row.get("avg_change_rate"))
                    if _number(row.get("avg_change_rate")) is not None
                    else -999.0
                ),
                -float(_number(row.get("breadth_pct")) or 0.0),
                str(row.get("theme_name") or ""),
            ),
        )
        for index, row in enumerate(average_rows, start=1):
            row["average_rank"] = index
            row["average_display_rank"] = index
            row["average_score"] = _number(row.get("avg_change_rate"))
            row["average_score_text"] = row.get("change_rate_text") or "-"
            row["average_grade"] = row.get("trend_grade") or row.get("grade")
            row["average_grade_class"] = row.get("trend_grade_class") or row.get(
                "grade_class"
            )
            row["average_state"] = row.get("trend_state") or row.get("state")
            row["average_state_text"] = row.get("trend_state_text") or row.get(
                "state_text"
            )
            row["average_state_class"] = row.get("trend_state_class") or row.get(
                "state_class"
            )

        payload["average_rows"] = average_rows
        ranking_views = payload.setdefault("ranking_views", {})
        if isinstance(ranking_views, dict):
            available = list(ranking_views.get("available") or [])
            if "average" not in available:
                available.insert(0, "average")
            ranking_views["available"] = available
            ranking_views["average_label"] = "평균등락률"

        ranking_policy = payload.setdefault("ranking_policy", {})
        if isinstance(ranking_policy, dict):
            ranking_policy["average"] = {
                "sort_key": "avg_change_rate",
                "direction": "desc",
                "other_metrics_used": False,
                "browser_calculation_allowed": False,
            }

        performance = payload.setdefault("performance_breakdown", {})
        if isinstance(performance, dict):
            performance["average_rank_ms"] = round(
                (time.perf_counter() - started) * 1000.0, 3
            )
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["average_theme_view"] = "server_avg_change_rate_desc"
        return payload

    builder_class.__call__ = call
    builder_class._stockboard_average_theme_view_installed = True


_STYLE_EXTENSION = r"""
<style>
/* THEMEBOARD_AVERAGE_VIEW_RESIZABLE_COLUMNS_20260714 */
.topbar{height:auto!important;min-height:0!important;max-height:none!important;overflow:visible!important}
.summary.theme-summary-compact{display:flex!important;align-items:center;gap:4px;padding:3px 5px;overflow-x:auto;white-space:nowrap}
.summary.theme-summary-compact .summary-card{display:inline-flex;align-items:center;gap:5px;flex:0 0 auto;min-height:24px;padding:3px 7px}
.summary.theme-summary-compact .summary-card span,.summary.theme-summary-compact .summary-card b{display:inline!important;margin:0!important;font-size:12px!important;white-space:nowrap}
.summary.theme-summary-compact .summary-top-card{display:none!important}
.theme-column-resizer{position:absolute;top:0;right:-4px;width:8px;height:100%;z-index:6;cursor:col-resize;touch-action:none}
.theme-column-resizer::after{content:"";position:absolute;top:4px;bottom:4px;left:3px;width:1px;background:#94a3b8;opacity:.45}
.theme-column-resizer:hover::after,.theme-column-resizer.dragging::after{width:2px;background:#2563eb;opacity:1}
#themeTable th,#detailTable th{position:sticky}
#themeTable,#detailTable{table-layout:fixed!important;min-width:0!important}
.theme-width-button{font-weight:900}
body.theme-column-dragging{cursor:col-resize!important;user-select:none!important}
</style>
"""


_SCRIPT_EXTENSION = r"""
<script>
/* THEMEBOARD_AVERAGE_VIEW_RESIZABLE_COLUMNS_20260714 */
(function(){
  const storedView=localStorage.getItem('aitrade.theme.view.v3');
  window.__themeBoardView=['average','momentum','money'].includes(storedView)?storedView:'average';

  __tbViewPrefix=function(){return window.__themeBoardView==='money'?'money':window.__themeBoardView==='average'?'average':'trend';};
  __tbServerRows=function(payload){
    if(window.__themeBoardView==='average')return Array.isArray(payload&&payload.average_rows)?payload.average_rows:[];
    if(window.__themeBoardView==='money')return Array.isArray(payload&&payload.money_rows)?payload.money_rows:[];
    return Array.isArray(payload&&payload.rows)?payload.rows:[];
  };
  __tbRank=function(theme){return window.__themeBoardView==='average'?(theme&&theme.average_display_rank??'-'):__tbField(theme,'display_rank',theme&&theme.display_rank);};
  __tbScore=function(theme){return window.__themeBoardView==='average'?(theme&&theme.change_rate_text||'-'):__tbField(theme,'score_text',theme&&theme.score_text);};
  __tbGrade=function(theme){return window.__themeBoardView==='average'?(theme&&theme.average_grade||theme&&theme.trend_grade||theme&&theme.grade):__tbField(theme,'grade',theme&&theme.grade);};
  __tbGradeClass=function(theme){return window.__themeBoardView==='average'?(theme&&theme.average_grade_class||theme&&theme.trend_grade_class||theme&&theme.grade_class):__tbField(theme,'grade_class',theme&&theme.grade_class);};
  __tbState=function(theme){return window.__themeBoardView==='average'?(theme&&theme.average_state_text||theme&&theme.trend_state_text||theme&&theme.state_text):__tbField(theme,'state_text',theme&&theme.state_text);};
  __tbStateClass=function(theme){return window.__themeBoardView==='average'?(theme&&theme.average_state_class||theme&&theme.trend_state_class||theme&&theme.state_class):__tbField(theme,'state_class',theme&&theme.state_class);};
  __tbViewLabel=function(){return window.__themeBoardView==='average'?'평균등락률':window.__themeBoardView==='money'?'돈쏠림':'상승탄력';};

  const oldApplyView=__tbApplyView;
  __tbApplyView=function(view){
    window.__themeBoardView=['average','momentum','money'].includes(view)?view:'average';
    localStorage.setItem('aitrade.theme.view.v3',window.__themeBoardView);
    ['Average','Momentum','Money'].forEach(name=>{
      const button=byId(`themeView${name}`);
      if(button)button.classList.toggle('active',window.__themeBoardView===name.toLowerCase());
    });
    if(lastPayload){renderSummary(lastPayload);renderThemes(lastPayload);}
  };

  const momentumButton=byId('themeViewMomentum');
  const averageButton=document.createElement('button');
  averageButton.id='themeViewAverage';
  averageButton.className='view-button';
  averageButton.type='button';
  averageButton.textContent='평균등락률';
  if(momentumButton&&momentumButton.parentElement){
    momentumButton.parentElement.insertBefore(averageButton,momentumButton);
    averageButton.addEventListener('click',()=>__tbApplyView('average'));
  }

  const summary=document.querySelector('.summary');
  if(summary){
    summary.classList.add('theme-summary-compact');
    const top=byId('summaryTop');
    if(top&&top.closest('.summary-card'))top.closest('.summary-card').classList.add('summary-top-card');
  }
  renderSummary=function(payload){
    byId('summaryCount').textContent=payload.theme_count??'-';
    byId('summaryFeature').textContent=`v${payload.input_feature_version??'-'}`;
    byId('summaryMaster').textContent=payload.mapping?.master_version||'-';
  };

  const widthButton=document.createElement('button');
  widthButton.id='themeColumnMinimize';
  widthButton.type='button';
  widthButton.className='control theme-width-button';
  widthButton.textContent='열 최소화';
  widthButton.title='전체 순위표와 상세표 열을 내용에 맞춰 최소화합니다. 헤더 경계는 드래그, 더블클릭은 해당 열 자동맞춤입니다.';
  const newWindowButton=byId('newWindow');
  if(newWindowButton)newWindowButton.insertAdjacentElement('afterend',widthButton);

  const tableStates=new Map();
  function widthStorageKey(table){return `aitrade.theme.columns.v2.${table.id}`;}
  function ensureCols(table){
    if(!table||!table.tHead||!table.tHead.rows.length)return [];
    let group=table.querySelector('colgroup');
    if(!group){group=document.createElement('colgroup');table.insertBefore(group,table.firstChild);}
    const count=table.tHead.rows[0].cells.length;
    while(group.children.length<count)group.appendChild(document.createElement('col'));
    while(group.children.length>count)group.lastElementChild.remove();
    return Array.from(group.children);
  }
  function widthBounds(table,index){
    if(table.id==='themeTable'){
      if(index===5)return [70,190];
      if(index===14)return [100,260];
      if([0,1,2,3,4].includes(index))return [38,76];
      return [48,135];
    }
    if(index===3)return [80,190];
    return [46,145];
  }
  function applyTableWidth(table){
    const cols=ensureCols(table);
    if(!cols.length)return;
    const total=cols.reduce((sum,col,index)=>{
      const bounds=widthBounds(table,index);
      const width=parseFloat(col.style.width)||bounds[0];
      return sum+width;
    },0);
    const wrap=table.closest('.table-wrap');
    table.style.width=`${Math.max(total,wrap?wrap.clientWidth:0)}px`;
    if(table.id==='themeTable'&&typeof __tbUpdateBottomScrollWidth==='function')__tbUpdateBottomScrollWidth();
  }
  function saveWidths(table){
    const widths=ensureCols(table).map(col=>Math.round(parseFloat(col.style.width)||0));
    localStorage.setItem(widthStorageKey(table),JSON.stringify(widths));
  }
  function restoreWidths(table){
    let widths=null;
    try{widths=JSON.parse(localStorage.getItem(widthStorageKey(table))||'null');}catch(_error){}
    const cols=ensureCols(table);
    if(!Array.isArray(widths)||widths.length!==cols.length)return false;
    widths.forEach((width,index)=>{if(Number.isFinite(Number(width))&&Number(width)>0)cols[index].style.width=`${Number(width)}px`;});
    applyTableWidth(table);
    return true;
  }
  function autoFitColumn(table,index,persist){
    const cols=ensureCols(table);
    if(!cols[index])return;
    const cells=[];
    if(table.tHead&&table.tHead.rows[0]&&table.tHead.rows[0].cells[index])cells.push(table.tHead.rows[0].cells[index]);
    Array.from(table.tBodies||[]).forEach(body=>Array.from(body.rows).slice(0,120).forEach(row=>{if(row.cells[index])cells.push(row.cells[index]);}));
    const [minimum,maximum]=widthBounds(table,index);
    const width=Math.max(minimum,Math.min(maximum,cells.reduce((value,cell)=>Math.max(value,cell.scrollWidth+14),minimum)));
    cols[index].style.width=`${Math.ceil(width)}px`;
    applyTableWidth(table);
    if(persist)saveWidths(table);
  }
  function minimizeTable(table,persist){
    ensureCols(table).forEach((_col,index)=>autoFitColumn(table,index,false));
    applyTableWidth(table);
    if(persist)saveWidths(table);
  }
  function installResizers(table){
    if(!table||table.dataset.columnResizeInstalled==='1')return;
    table.dataset.columnResizeInstalled='1';
    const cols=ensureCols(table);
    const headers=Array.from(table.tHead.rows[0].cells);
    headers.forEach((header,index)=>{
      const handle=document.createElement('span');
      handle.className='theme-column-resizer';
      handle.title='드래그: 열 폭 조절 · 더블클릭: 자동맞춤';
      header.appendChild(handle);
      handle.addEventListener('click',event=>event.stopPropagation());
      handle.addEventListener('dblclick',event=>{event.preventDefault();event.stopPropagation();autoFitColumn(table,index,true);});
      handle.addEventListener('mousedown',event=>{
        if(event.button!==0)return;
        event.preventDefault();event.stopPropagation();
        const startX=event.clientX;
        const startWidth=parseFloat(cols[index].style.width)||header.getBoundingClientRect().width;
        handle.classList.add('dragging');document.body.classList.add('theme-column-dragging');
        const move=moveEvent=>{
          const [minimum,maximum]=widthBounds(table,index);
          cols[index].style.width=`${Math.max(minimum,Math.min(maximum,startWidth+moveEvent.clientX-startX))}px`;
          applyTableWidth(table);
        };
        const up=()=>{
          document.removeEventListener('mousemove',move);
          document.removeEventListener('mouseup',up);
          handle.classList.remove('dragging');document.body.classList.remove('theme-column-dragging');saveWidths(table);
        };
        document.addEventListener('mousemove',move);
        document.addEventListener('mouseup',up);
      });
    });
    const restored=restoreWidths(table);
    const observer=new MutationObserver(()=>{
      if(!restored&&!tableStates.get(table)?.initialFitDone){
        const state=tableStates.get(table)||{};state.initialFitDone=true;tableStates.set(table,state);minimizeTable(table,false);
      }else applyTableWidth(table);
    });
    Array.from(table.tBodies||[]).forEach(body=>observer.observe(body,{childList:true,subtree:false}));
    tableStates.set(table,{observer,initialFitDone:restored});
    if(!restored)minimizeTable(table,false);
  }
  window.__tbMinimizeAllColumns=function(){
    ['themeTable','detailTable'].forEach(id=>{const table=byId(id);if(table)minimizeTable(table,true);});
  };
  widthButton.addEventListener('click',window.__tbMinimizeAllColumns);
  ['themeTable','detailTable'].forEach(id=>installResizers(byId(id)));

  window.addEventListener('resize',()=>['themeTable','detailTable'].forEach(id=>{const table=byId(id);if(table)applyTableWidth(table);}));
  __tbApplyView(window.__themeBoardView);
})();
</script>
"""


def _install_ui_extensions(ui_module) -> None:
    if getattr(ui_module, "_stockboard_average_view_layout_wrapped", False):
        return

    original_sort_value = ui_module._sort_value
    original_ordered_ids = ui_module._ordered_theme_ids

    def sort_value(row: dict[str, Any], view: str, key: str):
        if view == "average":
            if key == "current":
                return _number(row.get("average_rank"))
            if key in {"grade", "score", "average"}:
                return _number(row.get("avg_change_rate"))
        return original_sort_value(row, view, key)

    def ordered_ids(payload, *, view: str, key: str, direction: str):
        if view != "average":
            return original_ordered_ids(
                payload, view=view, key=key, direction=direction
            )
        rows = [
            row for row in payload.get("average_rows") or [] if isinstance(row, dict)
        ]
        normalized_key = key if key in ui_module._ALLOWED_SORT_KEYS else "current"
        normalized_direction = "desc" if direction == "desc" else "asc"
        valued = []
        missing = []
        for row in rows:
            value = sort_value(row, "average", normalized_key)
            if value is None or value == "":
                missing.append(row)
            else:
                valued.append((row, value))
        valued = sorted(
            valued,
            key=lambda item: (item[1], str(item[0].get("theme_name") or "")),
            reverse=normalized_direction == "desc",
        )
        return [
            str(row.get("theme_id") or "")
            for row in [item[0] for item in valued] + missing
            if str(row.get("theme_id") or "")
        ]

    ui_module._sort_value = sort_value
    ui_module._ordered_theme_ids = ordered_ids

    original_install = ui_module.install

    def install(base) -> None:
        if MARKER not in ui_module._STYLE:
            ui_module._STYLE += _STYLE_EXTENSION
        if MARKER not in ui_module._SCRIPT:
            ui_module._SCRIPT += _SCRIPT_EXTENSION
        original_install(base)

    ui_module.install = install
    ui_module._stockboard_average_view_layout_wrapped = True


def install_runtime_wrappers() -> None:
    rank_module = import_module("realtime_v2.theme_projection_dual_rank_patch")
    if not getattr(rank_module, "_stockboard_average_view_install_wrapped", False):
        original_install = rank_module.install

        def install(theme_module) -> None:
            original_install(theme_module)
            _install_average_rows(theme_module)

        rank_module.install = install
        rank_module._stockboard_average_view_install_wrapped = True

    ui_module = import_module("realtime_v2.worker_theme_dual_rank_ui_patch")
    _install_ui_extensions(ui_module)
