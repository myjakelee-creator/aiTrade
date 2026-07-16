from __future__ import annotations

MARKER = "STOCKBOARD_V2_MOMENTUM_BADGES_20260716"


def install() -> None:
    """Add one display-only momentum column with at most two horizontal badges."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_momentum_badge_html_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        column_anchor = "{ key:'large_trade_net_count', label:'대량체결', className:'num', sort:'large_trade_net_count', width:70, min:54 }"
        column_replacement = (
            column_anchor
            + ",\n    { key:'momentum_badges', label:'모멘텀', className:'center', sort:null, width:142, min:112 }"
        )
        if column_anchor not in patched:
            raise RuntimeError("momentum column anchor not found")
        patched = patched.replace(column_anchor, column_replacement, 1)

        width_anchor = "large_trade_net_count: 58\n  };"
        width_replacement = "large_trade_net_count: 58,\n    momentum_badges: 126\n  };"
        if width_anchor not in patched:
            raise RuntimeError("momentum width anchor not found")
        patched = patched.replace(width_anchor, width_replacement, 1)

        helper_anchor = "  function rowHtml(raw){"
        helpers = r'''
  function momentumBadgeClass(label){
    const text=String(label||'');
    if(text.includes('돌파'))return 'momentum-breakout';
    if(text.includes('붕괴'))return 'momentum-breakdown';
    if(text.includes('지지'))return 'momentum-support';
    if(text.includes('저항'))return 'momentum-resistance';
    return 'momentum-neutral';
  }
  function momentumHtml(r){
    const badges=Array.isArray(r.momentum_badges)?r.momentum_badges:[];
    if(!badges.length)return '-';
    const hold=r.momentum_closed_hold?' momentum-close-hold':'';
    return `<div class="momentum-badges${hold}">${badges.slice(0,2).map(item=>{
      const label=String(item?.label||'');
      return `<span class="momentum-badge ${momentumBadgeClass(label)}">${escapeHtml(label)}</span>`;
    }).join('')}</div>`;
  }
  function momentumTitle(r){
    const details=Array.isArray(r.momentum_details)?r.momentum_details:[];
    if(!details.length)return `모멘텀 ${r.momentum_status||'-'}`;
    return details.map(d=>{
      const f=v=>{const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:2}):'-';};
      return [
        `${d.label||'모멘텀'} · ${d.minute||'-'}`,
        `O ${f(d.open)} H ${f(d.high)} L ${f(d.low)} C ${f(d.close)}`,
        `VWAP ${f(d.vwap)} · 이전C ${f(d.previous_close)} · 이전VWAP ${f(d.previous_vwap)}`,
        `dayOpen ${f(d.day_open)} · ${d.vwap_quality||'-'}`
      ].join('\n');
    }).join('\n\n');
  }
'''
        if helper_anchor not in patched:
            raise RuntimeError("momentum helper anchor not found")
        patched = patched.replace(helper_anchor, helpers + helper_anchor, 1)

        cell_anchor = "<td class=\"num ${clsSigned(r.large_trade_net_count)}${cellFlashClass(code,'large_trade_net_count',r.large_trade_net_count)}\">${largeText}</td>"
        cell_replacement = (
            cell_anchor
            + "\n      <td class=\"center momentum-cell${cellFlashClass(code,'momentum_badges',JSON.stringify(r.momentum_badges||[]))}\" title=\"${escapeHtml(momentumTitle(r))}\">${momentumHtml(r)}</td>"
        )
        if cell_anchor not in patched:
            raise RuntimeError("momentum cell anchor not found")
        patched = patched.replace(cell_anchor, cell_replacement, 1)

        style = f'''\n<style id="stockboard-v2-momentum-badges">\n  /* {MARKER} */\n  .board td.momentum-cell {{ padding:1px 3px; overflow:visible; }}\n  .momentum-badges {{ display:flex; align-items:center; justify-content:center; gap:3px; white-space:nowrap; }}\n  .momentum-badges.momentum-close-hold {{ opacity:.58; filter:saturate(.78); }}\n  .momentum-badge {{ display:inline-block; min-width:48px; padding:1px 4px; border:1px solid transparent; border-radius:3px; font-size:10px; font-weight:800; line-height:15px; text-align:center; letter-spacing:-.25px; box-sizing:border-box; }}\n  .momentum-breakout {{ color:#fff; background:#c81e1e; border-color:#991b1b; }}\n  .momentum-support {{ color:#991b1b; background:#fee2e2; border-color:#f87171; }}\n  .momentum-breakdown {{ color:#fff; background:#1559b7; border-color:#123f83; }}\n  .momentum-resistance {{ color:#174ea6; background:#dbeafe; border-color:#60a5fa; }}\n  .momentum-neutral {{ color:#374151; background:#e5e7eb; border-color:#9ca3af; }}\n</style>\n'''
        if "</head>" not in patched:
            raise RuntimeError("momentum style anchor not found")
        return patched.replace("</head>", style + "</head>", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._momentum_badge_html_installed = True
