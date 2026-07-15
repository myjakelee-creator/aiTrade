from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MARKER = "THEMEBOARD_DUAL_SERVER_RANK_UI_20260713"

_ALLOWED_SORT_KEYS = {
    "current",
    "trend",
    "money",
    "grade",
    "score",
    "theme",
    "average",
    "breadth",
    "momentum_1m",
    "persistence_5m",
    "amount_ratio",
    "money_1m",
    "money_5m",
    "coverage",
    "leader",
}


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _sort_value(row: dict[str, Any], view: str, key: str) -> float | str | None:
    current_prefix = "money" if view == "money" else "trend"
    if key == "current":
        return _number(row.get(f"{current_prefix}_rank"))
    if key == "trend":
        return _number(row.get("trend_rank"))
    if key == "money":
        return _number(row.get("money_rank"))
    if key in {"grade", "score"}:
        return _number(row.get(f"{current_prefix}_score"))
    if key == "theme":
        return str(row.get("theme_name") or "").casefold()
    if key == "average":
        return _number(row.get("avg_change_rate"))
    if key == "breadth":
        return _number(row.get("breadth_pct"))
    if key == "momentum_1m":
        return _number(row.get("change_momentum_1m"))
    if key == "persistence_5m":
        return _number(row.get("change_persistence_5m"))
    if key == "amount_ratio":
        return _number(row.get("theme_amount_ratio"))
    if key == "money_1m":
        return _number(row.get("trade_value_1m_eok"))
    if key == "money_5m":
        return _number(row.get("trade_value_5m_eok"))
    if key == "coverage":
        return _number(row.get("coverage_pct"))
    if key == "leader":
        leaders = row.get("leaders")
        if isinstance(leaders, list) and leaders and isinstance(leaders[0], dict):
            return _number(leaders[0].get("leadership_score")) or _number(
                leaders[0].get("change_rate")
            )
    return None


def _ordered_theme_ids(
    payload: dict[str, Any],
    *,
    view: str,
    key: str,
    direction: str,
) -> list[str]:
    normalized_view = "money" if view == "money" else "momentum"
    normalized_key = key if key in _ALLOWED_SORT_KEYS else "current"
    normalized_direction = "desc" if direction == "desc" else "asc"
    raw_rows = (
        payload.get("money_rows") if normalized_view == "money" else payload.get("rows")
    )
    rows = [row for row in raw_rows or [] if isinstance(row, dict)]

    valued: list[tuple[dict[str, Any], float | str]] = []
    missing: list[dict[str, Any]] = []
    for row in rows:
        value = _sort_value(row, normalized_view, normalized_key)
        if value is None or value == "":
            missing.append(row)
        else:
            valued.append((row, value))

    valued = sorted(
        valued,
        key=lambda item: (item[1], str(item[0].get("theme_name") or "")),
        reverse=normalized_direction == "desc",
    )
    ordered_rows = [row for row, _value in valued] + missing
    return [
        str(row.get("theme_id") or "")
        for row in ordered_rows
        if str(row.get("theme_id") or "")
    ]


_STYLE = r"""
<style>
/* THEMEBOARD_DUAL_SERVER_RANK_UI_20260713 */
.view-toggle{display:inline-flex;gap:3px;margin-left:auto}.view-button{min-height:22px;padding:2px 9px;border:1px solid #94a3b8;border-radius:3px;background:#fff;color:#334155;font-weight:900;cursor:pointer}.view-button.active{border-color:#1d4ed8;background:#1d4ed8;color:#fff}.view-button:focus{outline:2px solid #93c5fd;outline-offset:1px}
.leader-item{display:inline-flex;align-items:center;gap:2px}.leader-rate{font-variant-numeric:tabular-nums}.detail-panel-inline{grid-column:1/-1;width:100%;min-width:0;margin:0}.detail-panel-inline .table-wrap{max-height:320px}.theme-list-layout{display:block;padding:0 5px 5px}.theme-list-layout>.panel{width:100%}
.theme-card .fund-flow{grid-template-columns:48px minmax(0,1fr) 108px}.theme-card .fund-flow>b{display:flex;align-items:center;justify-content:flex-end;gap:4px;white-space:nowrap}.fund-score{min-width:22px;color:#b91c1c;font-weight:900;font-variant-numeric:tabular-nums}.fund-amount{color:#374151;font-weight:800}.theme-card .fund-flow .fill{background:linear-gradient(90deg,#fecaca,#ef4444,#b91c1c)}
.theme-ranking-wrap{overflow-x:auto;overflow-y:auto;scrollbar-gutter:stable}#themeTable{min-width:1180px}.theme-bottom-scroll{position:sticky;bottom:0;z-index:5;height:17px;overflow-x:auto;overflow-y:hidden;border-top:1px solid #94a3b8;background:#f8fafc}.theme-bottom-scroll-inner{height:1px}.theme-sort-head{cursor:pointer;user-select:none;white-space:nowrap}.theme-sort-head::after{display:inline-block;min-width:11px;margin-left:3px;color:#64748b;content:"↕"}.theme-sort-head.sort-asc::after{color:#1d4ed8;content:"▲"}.theme-sort-head.sort-desc::after{color:#1d4ed8;content:"▼"}.theme-sort-head:focus{outline:2px solid #93c5fd;outline-offset:-2px}
</style>
"""

_SCRIPT = r"""
<script>
/* THEMEBOARD_DUAL_SERVER_RANK_UI_20260713 */
window.__themeBoardView = localStorage.getItem('aitrade.theme.view.v3') === 'money' ? 'money' : 'momentum';
window.__themeDetailOpen = Boolean(selectedThemeId);
window.__themeTableSort = {key:'current',direction:'asc',requestSeq:0};

function __tbViewPrefix(){return window.__themeBoardView === 'money' ? 'money' : 'trend';}
function __tbServerRows(payload){
  if(window.__themeBoardView === 'money') return Array.isArray(payload&&payload.money_rows)?payload.money_rows:[];
  return Array.isArray(payload&&payload.rows)?payload.rows:[];
}
function __tbField(theme,name,fallback){
  const key=`${__tbViewPrefix()}_${name}`;
  const value=theme&&theme[key];
  return value===undefined||value===null||value===''?(fallback===undefined?'-':fallback):value;
}
function __tbRank(theme){return __tbField(theme,'display_rank',theme&&theme.display_rank);}
function __tbScore(theme){return __tbField(theme,'score_text',theme&&theme.score_text);}
function __tbGrade(theme){return __tbField(theme,'grade',theme&&theme.grade);}
function __tbGradeClass(theme){return __tbField(theme,'grade_class',theme&&theme.grade_class);}
function __tbState(theme){return __tbField(theme,'state_text',theme&&theme.state_text);}
function __tbStateClass(theme){return __tbField(theme,'state_class',theme&&theme.state_class);}
function __tbViewLabel(){return window.__themeBoardView==='money'?'돈쏠림':'상승탄력';}
function __tbNumericTone(value){const number=Number(value);return Number.isFinite(number)?(number>0?'plus':number<0?'minus':'zero'):'zero';}
function __tbIsSelected(theme){return window.__themeDetailOpen&&String(theme&&theme.theme_id)===String(selectedThemeId);}

rows=function(payload){return __tbServerRows(payload);};

leadersHtml=function(theme){
  const items=Array.isArray(theme&&theme.leaders)?theme.leaders:[];
  if(!items.length)return '-';
  return items.map(item=>{
    const rateTone=tone(item&&item.change_rate_tone?item.change_rate_tone:__tbNumericTone(item&&item.change_rate));
    const rateText=item&&item.change_rate_text?item.change_rate_text:'-';
    return `<span class="leader-item"><button type="button" class="stock-link" data-code="${esc(item.stock_code)}">${esc(item.stock_name)}</button><span class="leader-rate ${rateTone}">${esc(rateText)}</span></span>`;
  }).join(' · ');
};

radarHtml=function(theme){
  const selected=__tbIsSelected(theme);
  return `<article class="theme-card ${selected?'selected':''}" data-theme-id="${esc(theme.theme_id)}" aria-expanded="${selected?'true':'false'}">
    <div class="card-head"><span class="rank">${esc(__tbRank(theme))}</span><span class="card-name">${esc(theme.theme_name)}</span><span class="grade ${gradeClass(__tbGradeClass(theme))}">${esc(__tbGrade(theme))}</span><span class="state ${stateClass(__tbStateClass(theme))}">${esc(__tbState(theme))}</span></div>
    <div class="flow fund-flow" title="전체 유효 테마 상대평가 · 최근대금 50 + 대금비 상대 20 + 대금비 절대 20 + 프로그램/대량체결 10"><span>1분쏠림</span><div class="track"><div class="fill" style="width:${pctWidth(theme.fund_flow_1m_bar_pct)}%"></div></div><b><span class="fund-score">${esc(theme.fund_flow_1m_text)}</span><span class="fund-amount">· ${esc(theme.trade_value_1m_text)}</span></b></div>
    <div class="flow fund-flow" title="전체 유효 테마 상대평가 · 최근대금 50 + 대금비 상대 20 + 대금비 절대 20 + 프로그램/대량체결 10"><span>5분쏠림</span><div class="track"><div class="fill" style="width:${pctWidth(theme.fund_flow_5m_bar_pct)}%"></div></div><b><span class="fund-score">${esc(theme.fund_flow_5m_text)}</span><span class="fund-amount">· ${esc(theme.trade_value_5m_text)}</span></b></div>
    <div class="card-sub"><span>${__tbViewLabel()} ${esc(__tbScore(theme))}</span><span>평균 <b class="${tone(theme.change_rate_tone)}">${esc(theme.change_rate_text)}</b></span><span>${esc(theme.breadth_text)}</span><span>1분탄력 ${esc(theme.change_momentum_1m_text)}</span><span>5분지속 ${esc(theme.change_persistence_5m_text)}</span><span>대금비 ${esc(theme.theme_amount_ratio_text)}</span></div>
    <div class="leaders"><b>주도</b> ${leadersHtml(theme)}</div>
  </article>`;
};

themeRowHtml=function(theme){
  return `<tr data-theme-id="${esc(theme.theme_id)}" class="${__tbIsSelected(theme)?'selected':''}">
    <td class="center">${esc(__tbRank(theme))}</td>
    <td class="center">${esc(theme.trend_display_rank)}</td>
    <td class="center">${esc(theme.money_display_rank)}</td>
    <td class="center"><span class="grade ${gradeClass(__tbGradeClass(theme))}">${esc(__tbGrade(theme))}</span></td>
    <td class="num">${esc(__tbScore(theme))}</td>
    <td class="name">${esc(theme.theme_name)}</td>
    <td class="num ${tone(theme.change_rate_tone)}">${esc(theme.change_rate_text)}</td>
    <td class="num">${esc(theme.breadth_text)}</td>
    <td class="num">${esc(theme.change_momentum_1m_text)}</td>
    <td class="num">${esc(theme.change_persistence_5m_text)}</td>
    <td class="num">${esc(theme.theme_amount_ratio_text)}</td>
    <td class="num ${tone(theme.trade_value_1m_tone)}">${esc(theme.trade_value_1m_text)}</td>
    <td class="num ${tone(theme.trade_value_5m_tone)}">${esc(theme.trade_value_5m_text)}</td>
    <td class="center"><span class="coverage ${coverageClass(theme.coverage_status)}">${esc(theme.coverage_text)}</span></td>
    <td>${leadersHtml(theme)}</td>
  </tr>`;
};

renderSummary=function(payload){
  const list=__tbServerRows(payload);
  const top=list[0]||{};
  byId('summaryCount').textContent=payload.theme_count??'-';
  byId('summaryTop').textContent=top.theme_name?`${__tbViewLabel()} · ${top.theme_name} · ${__tbGrade(top)} ${__tbScore(top)}`:'-';
  byId('summaryFeature').textContent=`v${payload.input_feature_version??'-'}`;
  byId('summaryMaster').textContent=payload.mapping?.master_version||'-';
};

function __tbDetailPanel(){
  const detailTable=byId('detailTable');
  return detailTable?detailTable.closest('section.panel'):null;
}

function __tbDetachDetailBeforeRadarRender(){
  const radar=byId('radar');
  const detail=__tbDetailPanel();
  if(radar&&detail&&detail.parentElement===radar)radar.insertAdjacentElement('afterend',detail);
}

function __tbGridColumnCount(radar){
  const template=window.getComputedStyle(radar).gridTemplateColumns||'';
  return Math.max(1,template.split(/\s+/).filter(Boolean).length||1);
}

function __tbPlaceDetailBelowSelectedCardRow(){
  const radar=byId('radar');
  const detail=__tbDetailPanel();
  const layout=document.querySelector('main.layout');
  if(!radar||!detail)return;
  detail.classList.add('detail-panel-inline');
  if(!window.__themeDetailOpen){detail.hidden=true;return;}
  detail.hidden=false;
  const cards=Array.from(radar.querySelectorAll(':scope > .theme-card'));
  const selectedIndex=cards.findIndex(card=>String(card.dataset.themeId||'')===String(selectedThemeId||''));
  if(selectedIndex<0){
    radar.insertAdjacentElement('afterend',detail);
  }else{
    const columns=__tbGridColumnCount(radar);
    const rowEndIndex=Math.min(cards.length-1,(Math.floor(selectedIndex/columns)+1)*columns-1);
    cards[rowEndIndex].insertAdjacentElement('afterend',detail);
  }
  if(layout)layout.classList.add('theme-list-layout');
}

function __tbBindCardThemes(){
  radarEl.querySelectorAll('[data-theme-id]').forEach(element=>{element.onclick=()=>selectTheme(element.dataset.themeId);});
  bindStockLinks(radarEl);
}
function __tbBindTableThemes(){
  themeBody.querySelectorAll('[data-theme-id]').forEach(element=>{element.onclick=()=>selectTheme(element.dataset.themeId);});
  bindStockLinks(themeBody);
}
function __tbRenderTableRows(list){
  themeBody.innerHTML=list.length?list.map(themeRowHtml).join(''):'<tr><td colspan="15" class="empty">유효 테마 데이터 없음</td></tr>';
  __tbBindTableThemes();
  __tbUpdateBottomScrollWidth();
}
function __tbUpdateSortHeader(){
  const state=window.__themeTableSort;
  document.querySelectorAll('#themeTable thead th[data-sort-key]').forEach(th=>{
    const active=th.dataset.sortKey===state.key;
    th.classList.toggle('sort-asc',active&&state.direction==='asc');
    th.classList.toggle('sort-desc',active&&state.direction==='desc');
    th.setAttribute('aria-sort',active?(state.direction==='asc'?'ascending':'descending'):'none');
  });
}
async function __tbRenderSortedTable(payload,list){
  const state=window.__themeTableSort;
  __tbUpdateSortHeader();
  if(state.key==='current'&&state.direction==='asc'){
    __tbRenderTableRows(list);
    return;
  }
  const requestSeq=++state.requestSeq;
  try{
    const query=new URLSearchParams({view:window.__themeBoardView,key:state.key,direction:state.direction,ts:String(Date.now())});
    const response=await fetch(`/api/v2/hub/theme/order?${query.toString()}`,{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const orderPayload=await response.json();
    if(requestSeq!==state.requestSeq)return;
    const byThemeId=new Map(list.map(row=>[String(row.theme_id||''),row]));
    const ordered=[];
    const used=new Set();
    (Array.isArray(orderPayload.theme_ids)?orderPayload.theme_ids:[]).forEach(themeId=>{
      const key=String(themeId||'');
      const row=byThemeId.get(key);
      if(row&&!used.has(key)){ordered.push(row);used.add(key);}
    });
    list.forEach(row=>{const key=String(row.theme_id||'');if(!used.has(key)){ordered.push(row);used.add(key);}});
    __tbRenderTableRows(ordered);
  }catch(_error){
    __tbRenderTableRows(list);
  }
}
function __tbDefaultSortDirection(key){return ['current','trend','money','theme'].includes(key)?'asc':'desc';}
function __tbToggleSort(key){
  const state=window.__themeTableSort;
  if(state.key===key)state.direction=state.direction==='asc'?'desc':'asc';
  else{state.key=key;state.direction=__tbDefaultSortDirection(key);}
  if(lastPayload)__tbRenderSortedTable(lastPayload,__tbServerRows(lastPayload));
}

renderThemes=function(payload){
  const list=__tbServerRows(payload);
  const cardList=list.slice(0,20);
  if(!selectedThemeId&&list[0]){
    selectedThemeId=String(list[0].theme_id||'');
    window.__themeDetailOpen=true;
  }
  __tbDetachDetailBeforeRadarRender();
  radarEl.innerHTML=cardList.length?cardList.map(radarHtml).join(''):'<div class="empty">유효 테마 데이터 없음</div>';
  const title=byId('themeRankingTitle');
  if(title) title.textContent=`${__tbViewLabel()} 전체 순위 · 서버 완성 순서 · 1초 latest-only`;
  __tbBindCardThemes();
  __tbRenderSortedTable(payload,list);
  __tbPlaceDetailBelowSelectedCardRow();
};

selectTheme=async function(themeId){
  const nextThemeId=String(themeId||'');
  if(!nextThemeId)return;
  if(nextThemeId===String(selectedThemeId||'')&&window.__themeDetailOpen){
    window.__themeDetailOpen=false;
    if(lastPayload)renderThemes(lastPayload);
    return;
  }
  selectedThemeId=nextThemeId;
  window.__themeDetailOpen=true;
  localStorage.setItem('aitrade.theme.selected.v2',selectedThemeId);
  if(lastPayload)renderThemes(lastPayload);
  await loadDetail(true);
};

loadDetail=async function(force){
  if(!window.__themeDetailOpen||!selectedThemeId||!lastPayload)return;
  const version=Number(lastPayload.projection_version||0);
  if(!force&&version===lastDetailVersion)return;
  try{
    const response=await fetch(`/api/v2/hub/theme/detail?theme_id=${encodeURIComponent(selectedThemeId)}&ts=${Date.now()}`,{cache:'no-store'});
    const payload=await response.json();
    if(response.status===202||payload.status==='BUILDING'){
      byId('detailTitle').textContent='선택 테마 상세 준비 중';
      detailBody.innerHTML='<tr><td colspan="15" class="empty">선택한 테마 1개만 background에서 준비 중입니다.</td></tr>';
      setTimeout(()=>loadDetail(true),350);
      return;
    }
    if(!response.ok)throw new Error(payload.error||`HTTP ${response.status}`);
    const theme=payload.theme||{};
    const members=Array.isArray(theme.members)?theme.members:[];
    lastDetailVersion=Number(payload.projection_version||version);
    byId('detailTitle').textContent=`${theme.theme_name||'선택 테마'} · ${theme.active_member_count??0}/${theme.master_member_count??0}종목 · Coverage ${theme.coverage_text||'-'} · ${payload.calculate_ms??'-'}ms`;
    detailBody.innerHTML=members.length?members.map(memberRowHtml).join(''):'<tr><td colspan="15" class="empty">유효 구성종목 없음</td></tr>';
    bindStockLinks(detailBody);
  }catch(error){
    detailBody.innerHTML=`<tr><td colspan="15" class="empty">상세 조회 실패: ${esc(error.message)}</td></tr>`;
  }
};

function __tbApplyView(view){
  window.__themeBoardView=view==='money'?'money':'momentum';
  localStorage.setItem('aitrade.theme.view.v3',window.__themeBoardView);
  const momentum=byId('themeViewMomentum');
  const money=byId('themeViewMoney');
  if(momentum)momentum.classList.toggle('active',window.__themeBoardView==='momentum');
  if(money)money.classList.toggle('active',window.__themeBoardView==='money');
  if(lastPayload){renderSummary(lastPayload);renderThemes(lastPayload);}
}

const themeTable=byId('themeTable');
const themeWrap=themeTable?themeTable.closest('.table-wrap'):null;
let themeBottomScroll=null;
let themeBottomScrollInner=null;
function __tbUpdateBottomScrollWidth(){
  if(themeTable&&themeBottomScrollInner)themeBottomScrollInner.style.width=`${themeTable.scrollWidth}px`;
}
function __tbInstallBottomScrollbar(){
  if(!themeWrap||themeBottomScroll)return;
  themeWrap.classList.add('theme-ranking-wrap');
  themeBottomScroll=document.createElement('div');
  themeBottomScroll.className='theme-bottom-scroll';
  themeBottomScroll.setAttribute('aria-label','테마 전체 순위 가로 스크롤');
  themeBottomScrollInner=document.createElement('div');
  themeBottomScrollInner.className='theme-bottom-scroll-inner';
  themeBottomScroll.appendChild(themeBottomScrollInner);
  themeWrap.insertAdjacentElement('afterend',themeBottomScroll);
  let syncing=false;
  themeWrap.addEventListener('scroll',()=>{if(syncing)return;syncing=true;themeBottomScroll.scrollLeft=themeWrap.scrollLeft;syncing=false;});
  themeBottomScroll.addEventListener('scroll',()=>{if(syncing)return;syncing=true;themeWrap.scrollLeft=themeBottomScroll.scrollLeft;syncing=false;});
  if(window.ResizeObserver)new ResizeObserver(__tbUpdateBottomScrollWidth).observe(themeTable);
  __tbUpdateBottomScrollWidth();
}

let __tbResizeTimer=0;
window.addEventListener('resize',()=>{
  clearTimeout(__tbResizeTimer);
  __tbResizeTimer=setTimeout(()=>{__tbPlaceDetailBelowSelectedCardRow();__tbUpdateBottomScrollWidth();},120);
});

const themeHead=document.querySelector('#themeTable thead tr');
if(themeHead)themeHead.innerHTML='<th class="theme-sort-head" data-sort-key="current" tabindex="0">현재</th><th class="theme-sort-head" data-sort-key="trend" tabindex="0">상승</th><th class="theme-sort-head" data-sort-key="money" tabindex="0">돈</th><th class="theme-sort-head" data-sort-key="grade" tabindex="0">등급</th><th class="theme-sort-head" data-sort-key="score" tabindex="0">점수</th><th class="theme-sort-head" data-sort-key="theme" tabindex="0">테마</th><th class="theme-sort-head" data-sort-key="average" tabindex="0">평균등락</th><th class="theme-sort-head" data-sort-key="breadth" tabindex="0">확산</th><th class="theme-sort-head" data-sort-key="momentum_1m" tabindex="0">1분탄력</th><th class="theme-sort-head" data-sort-key="persistence_5m" tabindex="0">5분지속</th><th class="theme-sort-head" data-sort-key="amount_ratio" tabindex="0">대금비</th><th class="theme-sort-head" data-sort-key="money_1m" tabindex="0">1분대금</th><th class="theme-sort-head" data-sort-key="money_5m" tabindex="0">5분대금</th><th class="theme-sort-head" data-sort-key="coverage" tabindex="0">Coverage</th><th class="theme-sort-head" data-sort-key="leader" tabindex="0">주도주</th>';
document.querySelectorAll('#themeTable thead th[data-sort-key]').forEach(th=>{
  th.addEventListener('click',()=>__tbToggleSort(th.dataset.sortKey));
  th.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();__tbToggleSort(th.dataset.sortKey);}});
});
const momentumButton=byId('themeViewMomentum');
const moneyButton=byId('themeViewMoney');
if(momentumButton)momentumButton.addEventListener('click',()=>__tbApplyView('momentum'));
if(moneyButton)moneyButton.addEventListener('click',()=>__tbApplyView('money'));
__tbInstallBottomScrollbar();
__tbUpdateSortHeader();
__tbPlaceDetailBelowSelectedCardRow();
__tbApplyView(window.__themeBoardView);
</script>
"""


def _patch_html(html: str) -> str:
    if MARKER in html:
        return html
    html = html.replace("</head>", f"{_STYLE}\n</head>", 1)
    html = html.replace(
        '<div class="section-head">상위 테마 레이더 <span class="section-note">서버 순위·점수·막대 그대로 표시</span></div>',
        '<div class="section-head">상위 테마 레이더 <span class="section-note">현재 보기 상위 20개 · 1분/5분 쏠림은 전체 테마 상대평가</span><span class="view-toggle"><button id="themeViewMomentum" class="view-button active" type="button">상승탄력</button><button id="themeViewMoney" class="view-button" type="button">돈쏠림</button></span></div>',
        1,
    )
    html = html.replace(
        '<h2>테마 돈쏠림 순위 · 1초 공용 Projection</h2>',
        '<h2 id="themeRankingTitle">상승탄력 전체 순위 · 서버 완성 순서 · 1초 latest-only</h2>',
        1,
    )
    html = html.replace("</body>", f"{_SCRIPT}\n</body>", 1)
    return html


def install(base) -> None:
    handler_class = base.WebHandler
    if getattr(handler_class, "_stockboard_theme_dual_rank_ui_installed", False):
        return
    original_do_get = handler_class.do_GET

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path == "/api/v2/hub/theme/order":
            query = base.parse_qs(parsed.query)
            view = str((query.get("view") or ["momentum"])[0] or "momentum")
            key = str((query.get("key") or ["current"])[0] or "current")
            direction = str((query.get("direction") or ["asc"])[0] or "asc")
            hub = getattr(self.server.state, "board_data_hub", None)
            projection = hub.projection_snapshot("theme") if hub is not None else None
            payload = (
                projection.get("payload")
                if isinstance(projection, dict)
                and isinstance(projection.get("payload"), dict)
                else None
            )
            if not isinstance(payload, dict) or payload.get("status") != "READY":
                self._json(
                    {"error": "theme projection unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self._json(
                {
                    "schema_version": 1,
                    "source": "theme_cached_server_sort_order",
                    "projection_version": projection.get("projection_version"),
                    "view": "money" if view == "money" else "momentum",
                    "key": key if key in _ALLOWED_SORT_KEYS else "current",
                    "direction": "desc" if direction == "desc" else "asc",
                    "theme_ids": _ordered_theme_ids(
                        payload,
                        view=view,
                        key=key,
                        direction=direction,
                    ),
                    "policy": {
                        "cached_projection_only": True,
                        "direct_tr_allowed": False,
                        "direct_openapi_allowed": False,
                        "browser_sort_allowed": False,
                    },
                }
            )
            return
        if parsed.path not in {"/theme", "/themeboard", "/themeboard.html"}:
            return original_do_get(self)
        path = ROOT / "docs" / "themeboard.html"
        if not path.is_file():
            return original_do_get(self)
        body = _patch_html(path.read_text(encoding="utf-8")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    handler_class.do_GET = patched_do_get
    handler_class._stockboard_theme_dual_rank_ui_installed = True
