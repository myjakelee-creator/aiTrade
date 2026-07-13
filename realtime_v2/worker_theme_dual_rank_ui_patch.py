from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKER = "THEMEBOARD_DUAL_SERVER_RANK_UI_20260713"

_STYLE = r"""
<style>
/* THEMEBOARD_DUAL_SERVER_RANK_UI_20260713 */
.view-toggle{display:inline-flex;gap:3px;margin-left:auto}.view-button{min-height:22px;padding:2px 9px;border:1px solid #94a3b8;border-radius:3px;background:#fff;color:#334155;font-weight:900;cursor:pointer}.view-button.active{border-color:#1d4ed8;background:#1d4ed8;color:#fff}.view-button:focus{outline:2px solid #93c5fd;outline-offset:1px}
.leader-item{display:inline-flex;align-items:center;gap:2px}.leader-rate{font-variant-numeric:tabular-nums}.detail-panel-inline{margin:0 5px 5px}.detail-panel-inline .table-wrap{max-height:320px}.theme-list-layout{display:block;padding:0 5px 5px}.theme-list-layout>.panel{width:100%}
</style>
"""

_SCRIPT = r"""
<script>
/* THEMEBOARD_DUAL_SERVER_RANK_UI_20260713 */
window.__themeBoardView = localStorage.getItem('aitrade.theme.view.v3') === 'money' ? 'money' : 'momentum';

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
  return `<article class="theme-card ${String(theme.theme_id)===selectedThemeId?'selected':''}" data-theme-id="${esc(theme.theme_id)}">
    <div class="card-head"><span class="rank">${esc(__tbRank(theme))}</span><span class="card-name">${esc(theme.theme_name)}</span><span class="grade ${gradeClass(__tbGradeClass(theme))}">${esc(__tbGrade(theme))}</span><span class="state ${stateClass(__tbStateClass(theme))}">${esc(__tbState(theme))}</span></div>
    <div class="flow"><span>1분</span><div class="track"><div class="fill" style="width:${pctWidth(theme.trade_value_1m_bar_pct)}%"></div></div><b class="${tone(theme.trade_value_1m_tone)}">${esc(theme.trade_value_1m_text)}</b></div>
    <div class="flow"><span>5분</span><div class="track"><div class="fill" style="width:${pctWidth(theme.trade_value_5m_bar_pct)}%"></div></div><b class="${tone(theme.trade_value_5m_tone)}">${esc(theme.trade_value_5m_text)}</b></div>
    <div class="card-sub"><span>${__tbViewLabel()} ${esc(__tbScore(theme))}</span><span>평균 <b class="${tone(theme.change_rate_tone)}">${esc(theme.change_rate_text)}</b></span><span>${esc(theme.breadth_text)}</span><span>1분탄력 ${esc(theme.change_momentum_1m_text)}</span><span>5분지속 ${esc(theme.change_persistence_5m_text)}</span><span>대금비 ${esc(theme.theme_amount_ratio_text)}</span></div>
    <div class="leaders"><b>주도</b> ${leadersHtml(theme)}</div>
  </article>`;
};

themeRowHtml=function(theme){
  return `<tr data-theme-id="${esc(theme.theme_id)}" class="${String(theme.theme_id)===selectedThemeId?'selected':''}">
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

renderThemes=function(payload){
  const list=__tbServerRows(payload);
  const cardList=list.slice(0,10);
  if(!selectedThemeId&&list[0])selectedThemeId=String(list[0].theme_id||'');
  radarEl.innerHTML=cardList.length?cardList.map(radarHtml).join(''):'<div class="empty">유효 테마 데이터 없음</div>';
  themeBody.innerHTML=list.length?list.map(themeRowHtml).join(''):'<tr><td colspan="15" class="empty">유효 테마 데이터 없음</td></tr>';
  const title=byId('themeRankingTitle');
  if(title) title.textContent=`${__tbViewLabel()} 전체 순위 · 서버 완성 순서 · 1초 latest-only`;
  bindThemes();
};

loadDetail=async function(force){
  if(!selectedThemeId||!lastPayload)return;
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

function __tbMoveDetailBelowRadar(){
  const radar=byId('radar');
  const detailTable=byId('detailTable');
  const detail=detailTable?detailTable.closest('section.panel'):null;
  const layout=document.querySelector('main.layout');
  if(radar&&detail&&detail.previousElementSibling!==radar){
    detail.classList.add('detail-panel-inline');
    radar.insertAdjacentElement('afterend',detail);
  }
  if(layout)layout.classList.add('theme-list-layout');
}

function __tbApplyView(view){
  window.__themeBoardView=view==='money'?'money':'momentum';
  localStorage.setItem('aitrade.theme.view.v3',window.__themeBoardView);
  const momentum=byId('themeViewMomentum');
  const money=byId('themeViewMoney');
  if(momentum)momentum.classList.toggle('active',window.__themeBoardView==='momentum');
  if(money)money.classList.toggle('active',window.__themeBoardView==='money');
  if(lastPayload){renderSummary(lastPayload);renderThemes(lastPayload);}
}

const themeHead=document.querySelector('#themeTable thead tr');
if(themeHead)themeHead.innerHTML='<th>현재</th><th>상승</th><th>돈</th><th>등급</th><th>점수</th><th>테마</th><th>평균등락</th><th>확산</th><th>1분탄력</th><th>5분지속</th><th>대금비</th><th>1분대금</th><th>5분대금</th><th>Coverage</th><th>주도주</th>';
const momentumButton=byId('themeViewMomentum');
const moneyButton=byId('themeViewMoney');
if(momentumButton)momentumButton.addEventListener('click',()=>__tbApplyView('momentum'));
if(moneyButton)moneyButton.addEventListener('click',()=>__tbApplyView('money'));
__tbMoveDetailBelowRadar();
__tbApplyView(window.__themeBoardView);
</script>
"""


def _patch_html(html: str) -> str:
    if MARKER in html:
        return html
    html = html.replace("</head>", f"{_STYLE}\n</head>", 1)
    html = html.replace(
        '<div class="section-head">상위 테마 레이더 <span class="section-note">서버 순위·점수·막대 그대로 표시</span></div>',
        '<div class="section-head">상위 테마 레이더 <span class="section-note">현재 보기 상위 10개 · 전체 테마는 하단 순위표</span><span class="view-toggle"><button id="themeViewMomentum" class="view-button active" type="button">상승탄력</button><button id="themeViewMoney" class="view-button" type="button">돈쏠림</button></span></div>',
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
