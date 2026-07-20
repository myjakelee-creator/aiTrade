from __future__ import annotations

import re

MARKER = "STOCKBOARD_V2_MOMENTUM_GRADE_ALERTS_20260717"


def _strip_non_candle_tooltips(html: str) -> str:
    preserved: list[tuple[str, str]] = []

    def preserve_candle_title(match: re.Match[str]) -> str:
        token = f"__STOCKBOARD_DAILY_CANDLE_TITLE_{len(preserved)}__"
        preserved.append((token, match.group("title")))
        return f'{match.group("prefix")} {token}'

    html = re.sub(
        r'(?P<prefix><div class="mini-candle[^"]*")\s+(?P<title>title="[^"]*")',
        preserve_candle_title,
        html,
    )
    if not preserved:
        raise RuntimeError("daily candle tooltip anchor not found")
    html = re.sub(r"\s+title=\"[^\"]*\"", "", html)
    html = re.sub(r"\n\s*uiZoomToggle\.title\s*=\s*'[^']*';", "", html)
    html = re.sub(
        r"\n\s*rowPositionToggle\.title\s*=\s*paused.*?;",
        "",
        html,
        count=1,
        flags=re.DOTALL,
    )
    html = re.sub(r"\n\s*el\.title\s*=\s*`[^`]*`;", "", html)
    for token, title in preserved:
        html = html.replace(token, title)
    return html


def install() -> None:
    """Use the grade cell for momentum and add one lightweight global alert strip."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_momentum_badge_html_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        # Remove the earlier separate momentum-column experiment when present.
        patched = patched.replace(
            ",\n    { key:'momentum_badges', label:'모멘텀', className:'center', sort:null, width:142, min:112 }",
            "",
        )
        patched = patched.replace(",\n    momentum_badges: 126", "")
        patched = re.sub(
            r"\n\s*<td class=\"center momentum-cell.*?</td>",
            "",
            patched,
            count=1,
        )

        topbar_anchor = '<div id="topbar" class="topbar">'
        topbar_replacement = topbar_anchor + '''
    <div id="momentum-alert-strip" class="momentum-alert-strip">
      <span class="momentum-alert-empty">모멘텀 신호 없음</span>
    </div>'''
        if topbar_anchor not in patched:
            raise RuntimeError("momentum topbar anchor not found")
        patched = patched.replace(topbar_anchor, topbar_replacement, 1)

        element_anchor = "  const marketSupplyRow = document.getElementById('market-supply-row');"
        if element_anchor not in patched:
            raise RuntimeError("momentum element anchor not found")
        patched = patched.replace(
            element_anchor,
            element_anchor
            + "\n  const momentumAlertStripEl = document.getElementById('momentum-alert-strip');",
            1,
        )

        grade_start = patched.find("  function gradeHtml(r){")
        grade_end = patched.find("  function deriveClientFields(row){", grade_start)
        if grade_start < 0 or grade_end < 0:
            raise RuntimeError("grade helper anchor not found")
        helpers = r'''  function gradeHtml(r){
    const t=r.candidate_grade_text||r.grade_text||r.grade||r.candidate_grade||'-';
    const l=String(t).slice(0,1).toLowerCase();
    return `<span class="grade ${['a','b','c','d','f'].includes(l)?l:''}">${escapeHtml(t)}</span>`;
  }
  function momentumToneClass(tone){
    const text=String(tone||'');
    if(text==='strong_up')return 'momentum-strong-up';
    if(text==='strong_down')return 'momentum-strong-down';
    if(text==='support')return 'momentum-support';
    if(text==='resistance')return 'momentum-resistance';
    return 'momentum-neutral';
  }
  function momentumGradeHtml(r){
    const badges=Array.isArray(r.momentum_badges)?r.momentum_badges.slice(0,2):[];
    if(!badges.length)return gradeHtml(r);
    const alternate=Math.max(400,Number(r.momentum_badge_alternate_ms)||1200);
    const cycle=alternate*2;
    const dual=badges.length>1?' dual':'';
    return `<span class="momentum-grade-stack${dual}" style="--momentum-cycle:${cycle}ms">${badges.map(item=>{
      const phase=String(item?.phase||'active');
      const badge=String(item?.badge||'');
      return `<span class="momentum-grade-badge ${momentumToneClass(item?.tone)} momentum-${escapeHtml(phase)}">${escapeHtml(badge)}</span>`;
    }).join('')}</span>`;
  }
  let momentumAlertVersion=-1;
  let momentumAlertFetchVersion=-1;
  let momentumAlertItems=[];
  let momentumAlertPageSize=4;
  let momentumAlertRotateMs=1800;
  let momentumAlertPage=0;
  let momentumAlertNextRotateAt=0;
  function momentumAlertBadgeHtml(item){
    const badges=Array.isArray(item?.badges)?item.badges:[];
    return badges.map(b=>`<span class="momentum-alert-badge ${momentumToneClass(b?.tone)} momentum-${escapeHtml(String(b?.phase||'active'))}">${escapeHtml(String(b?.badge||''))}</span>`).join('');
  }
  function renderMomentumAlertPage(){
    if(!momentumAlertStripEl)return;
    if(!momentumAlertItems.length){
      momentumAlertStripEl.innerHTML='<span class="momentum-alert-empty">모멘텀 신호 없음</span>';
      return;
    }
    const pages=Math.max(1,Math.ceil(momentumAlertItems.length/momentumAlertPageSize));
    momentumAlertPage=((momentumAlertPage%pages)+pages)%pages;
    const start=momentumAlertPage*momentumAlertPageSize;
    const page=momentumAlertItems.slice(start,start+momentumAlertPageSize);
    momentumAlertStripEl.innerHTML=`<span class="momentum-alert-count">모멘텀 ${momentumAlertItems.length}종목</span>${page.map(item=>`<span class="momentum-alert-item"><span class="momentum-alert-name">${escapeHtml(String(item.stock_name||item.stock_code||'-'))}</span>${momentumAlertBadgeHtml(item)}</span>`).join('')}`;
  }
  function tickMomentumAlertRotation(){
    if(momentumAlertItems.length<=momentumAlertPageSize)return;
    const now=Date.now();
    if(now<momentumAlertNextRotateAt)return;
    momentumAlertPage+=1;
    momentumAlertNextRotateAt=now+momentumAlertRotateMs;
    renderMomentumAlertPage();
  }
  async function fetchMomentumAlerts(version){
    if(version===momentumAlertFetchVersion)return;
    momentumAlertFetchVersion=version;
    try{
      const response=await fetch(`/api/v2/momentum_alerts?version=${encodeURIComponent(version)}&ts=${Date.now()}`,{cache:'no-store'});
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const payload=await response.json();
      if(Number(payload.version)!==version)return;
      momentumAlertItems=Array.isArray(payload.items)?payload.items:[];
      momentumAlertPageSize=Math.max(1,Number(payload.page_size)||4);
      momentumAlertRotateMs=Math.max(500,Number(payload.rotate_interval_ms)||1800);
      momentumAlertPage=0;
      momentumAlertNextRotateAt=Date.now()+momentumAlertRotateMs;
      renderMomentumAlertPage();
    }catch(_error){
      if(momentumAlertStripEl)momentumAlertStripEl.innerHTML='<span class="momentum-alert-empty">모멘텀 알림 연결 대기</span>';
    }
  }
  function updateMomentumAlertSummary(payload){
    const version=Number(payload?.status?.momentum_alert_version);
    if(!Number.isFinite(version)||version<0)return;
    if(version!==momentumAlertVersion){
      momentumAlertVersion=version;
      fetchMomentumAlerts(version);
    }
    tickMomentumAlertRotation();
  }
'''
        patched = patched[:grade_start] + helpers + patched[grade_end:]

        grade_cell = "<td class=\"center${cellFlashClass(code,'grade',r.candidate_grade_text||r.grade_text||r.grade||r.candidate_grade||'-')}\">${gradeHtml(r)}</td>"
        grade_replacement = "<td class=\"center momentum-grade-cell${cellFlashClass(code,'grade',JSON.stringify(r.momentum_badges||[])+'|'+(r.candidate_grade_text||r.grade_text||r.grade||r.candidate_grade||'-'))}\">${momentumGradeHtml(r)}</td>"
        if grade_cell not in patched:
            raise RuntimeError("grade cell anchor not found")
        patched = patched.replace(grade_cell, grade_replacement, 1)

        render_anchor = "lastPayload=payload;markSortHeaders();"
        if render_anchor not in patched:
            raise RuntimeError("momentum render anchor not found")
        patched = patched.replace(
            render_anchor,
            "lastPayload=payload;updateMomentumAlertSummary(payload);markSortHeaders();",
            1,
        )
        patched = patched.replace(
            "setInterval(()=>{clockEl.textContent=",
            "setInterval(()=>{tickMomentumAlertRotation();clockEl.textContent=",
            1,
        )

        style = f'''
<style id="stockboard-v2-momentum-grade-alerts">
  /* {MARKER} */
  .momentum-alert-strip {{ display:flex; align-items:center; gap:7px; width:100%; min-width:0; height:22px; overflow:hidden; border:1px solid #9aa8b5; border-radius:3px; padding:1px 6px; background:#f8fafc; box-sizing:border-box; white-space:nowrap; }}
  .momentum-alert-empty {{ color:#6b7280; font-weight:700; }}
  .momentum-alert-count {{ flex:0 0 auto; color:#111827; font-weight:800; }}
  .momentum-alert-item {{ display:inline-flex; align-items:center; gap:3px; min-width:0; }}
  .momentum-alert-name {{ max-width:112px; overflow:hidden; text-overflow:ellipsis; color:#111827; font-weight:800; }}
  .momentum-alert-badge, .momentum-grade-badge {{ display:inline-flex; align-items:center; justify-content:center; min-width:31px; height:16px; padding:0 3px; border:1px solid transparent; border-radius:3px; box-sizing:border-box; font-size:10px; font-weight:900; line-height:14px; letter-spacing:-.35px; }}
  .momentum-grade-cell {{ padding:1px 2px !important; overflow:visible !important; }}
  .momentum-grade-stack {{ position:relative; display:inline-flex; align-items:center; justify-content:center; min-width:34px; height:16px; vertical-align:middle; }}
  .momentum-grade-stack.dual .momentum-grade-badge {{ position:absolute; inset:0 auto auto 50%; transform:translateX(-50%); animation:stockboardMomentumAlternate var(--momentum-cycle) linear infinite; }}
  .momentum-grade-stack.dual .momentum-grade-badge:nth-child(2) {{ animation-delay:calc(var(--momentum-cycle) / -2); }}
  .momentum-strong-up {{ color:#fff; background:#c81e1e; border-color:#991b1b; }}
  .momentum-support {{ color:#991b1b; background:#fee2e2; border-color:#f87171; }}
  .momentum-strong-down {{ color:#fff; background:#1559b7; border-color:#123f83; }}
  .momentum-resistance {{ color:#174ea6; background:#dbeafe; border-color:#60a5fa; }}
  .momentum-neutral {{ color:#374151; background:#e5e7eb; border-color:#9ca3af; }}
  .momentum-grace {{ opacity:.86; }}
  .momentum-fading {{ opacity:.42; }}
  @keyframes stockboardMomentumAlternate {{ 0%,45%{{opacity:1}} 50%,95%{{opacity:0}} 100%{{opacity:1}} }}
</style>
'''
        if "</head>" not in patched:
            raise RuntimeError("momentum style anchor not found")
        patched = patched.replace("</head>", style + "</head>", 1)
        return _strip_non_candle_tooltips(patched)

    large._ui_safety_patch = patched_ui_safety_patch
    large._momentum_badge_html_installed = True
