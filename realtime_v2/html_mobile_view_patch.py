from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "stockboard_view_modes.json"
MARKER = "STOCKBOARD_V2_RESPONSIVE_MOBILE_VIEW_20260717"


def _load_config() -> dict[str, Any]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("stockboard view mode config must be an object")

    columns = [str(item) for item in payload.get("mobile_columns") or []]
    expected = [
        "rank",
        "rank_change",
        "grade",
        "stock_name",
        "change_rate",
        "amount_ratio",
        "execution_strength",
        "program_net",
    ]
    if columns != expected:
        raise ValueError("mobile_columns must match the approved eight-column order")

    raw_widths = payload.get("mobile_column_width_percent")
    if not isinstance(raw_widths, dict):
        raise ValueError("mobile_column_width_percent must be an object")
    widths = {key: float(raw_widths.get(key) or 0) for key in columns}
    if any(value <= 0 for value in widths.values()) or abs(sum(widths.values()) - 100.0) > 0.01:
        raise ValueError("mobile column width percentages must be positive and sum to 100")

    market = payload.get("mobile_market") if isinstance(payload.get("mobile_market"), dict) else {}
    behavior = payload.get("mobile_behavior") if isinstance(payload.get("mobile_behavior"), dict) else {}
    if not bool(behavior.get("preserve_themeboard", True)):
        raise ValueError("ThemeBoard preservation must remain enabled")

    return {
        "schema_version": int(payload.get("schema_version") or 1),
        "auto_mobile_max_width_px": max(480, min(1200, int(payload.get("auto_mobile_max_width_px") or 760))),
        "mobile_columns": columns,
        "mobile_column_width_percent": widths,
        "mobile_market": {
            "us_min_visible_columns": max(2, int(market.get("us_min_visible_columns") or 4)),
            "domestic_min_visible_columns": max(2, int(market.get("domestic_min_visible_columns") or 4)),
            "momentum_alert_page_size": max(1, int(market.get("momentum_alert_page_size") or 1)),
        },
        "mobile_behavior": {
            "disable_hts_clipboard_link": bool(behavior.get("disable_hts_clipboard_link", True)),
            "hide_strategyboard_entry": bool(behavior.get("hide_strategyboard_entry", True)),
            "preserve_themeboard": True,
            "reload_on_mode_change": bool(behavior.get("reload_on_mode_change", True)),
        },
    }


def _replace_columns(html: str, config_json: str) -> str:
    start = html.find("  const columns = [")
    end_anchor = "  ];\n\n  let lastPayload"
    end = html.find(end_anchor, start)
    if start < 0 or end < 0:
        raise RuntimeError("StockBoard columns declaration not found")
    declaration_end = end + len("  ];")
    desktop_declaration = html[start:declaration_end].replace(
        "  const columns = [", "  const desktopColumns = [", 1
    )
    responsive = f'''{desktop_declaration}
  const STOCKBOARD_VIEW_CONFIG = {config_json};
  const STOCKBOARD_VIEW_OVERRIDE_KEY = 'stockboard.v2.viewOverride.session.v1';
  function stockboardViewportWidth(){{
    return Math.max(0, Number(window.innerWidth)||Number(document.documentElement.clientWidth)||0);
  }}
  function stockboardAutomaticMode(width=stockboardViewportWidth()){{
    return width <= Number(STOCKBOARD_VIEW_CONFIG.auto_mobile_max_width_px||760) ? 'mobile' : 'desktop';
  }}
  function stockboardResolveViewMode(){{
    const override=sessionStorage.getItem(STOCKBOARD_VIEW_OVERRIDE_KEY);
    return override==='mobile'||override==='desktop' ? override : stockboardAutomaticMode();
  }}
  const stockboardViewMode=stockboardResolveViewMode();
  const mobileColumnKeys=new Set(STOCKBOARD_VIEW_CONFIG.mobile_columns||[]);
  let columns=stockboardViewMode==='mobile'
    ? desktopColumns.filter(column=>mobileColumnKeys.has(column.key))
    : desktopColumns;
  document.documentElement.classList.toggle('stockboard-mobile',stockboardViewMode==='mobile');
  document.documentElement.classList.toggle('stockboard-desktop',stockboardViewMode!=='mobile');'''
    return html[:start] + responsive + html[declaration_end:]


def install() -> None:
    """Install a display-only responsive StockBoard mobile mode.

    The patch does not add data requests, worker calculations, WebSockets or worker
    threads. Mobile mode builds only the approved eight table cells. ThemeBoard is
    deliberately outside every selector and code path in this patch.
    """

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_responsive_mobile_view_installed", False):
        return

    config = _load_config()
    config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        toggle_anchor = '<span id="clock" class="badge">-</span>'
        if toggle_anchor not in patched:
            raise RuntimeError("StockBoard clock anchor not found")
        patched = patched.replace(
            toggle_anchor,
            toggle_anchor
            + '<button type="button" id="stockboard-view-toggle" class="control stockboard-view-toggle">모바일 보기</button>',
            1,
        )

        patched = _replace_columns(patched, config_json)

        element_anchor = "  const clockEl = document.getElementById('clock');"
        if element_anchor not in patched:
            raise RuntimeError("StockBoard clock element anchor not found")
        patched = patched.replace(
            element_anchor,
            element_anchor
            + "\n  const stockboardViewToggle = document.getElementById('stockboard-view-toggle');",
            1,
        )

        old_column_width = "function columnWidth(i){ const c=columns[i]||{}; const saved=Number(columnWidths[c.key]); return Number.isFinite(saved)&&saved>0?saved:Number(c.width||70); }"
        new_column_width = "function columnWidth(i){ const c=columns[i]||{}; if(stockboardViewMode==='mobile'){const pct=Number(STOCKBOARD_VIEW_CONFIG.mobile_column_width_percent?.[c.key])||0;return Math.max(18,Math.floor(stockboardViewportWidth()*pct/100));} const saved=Number(columnWidths[c.key]); return Number.isFinite(saved)&&saved>0?saved:Number(c.width||70); }"
        if old_column_width not in patched:
            raise RuntimeError("StockBoard columnWidth anchor not found")
        patched = patched.replace(old_column_width, new_column_width, 1)

        old_set_width = "function setColumnWidth(i,w,save=true){ const c=columns[i]; if(!c)return; const px=clampWidth(i,w); document.querySelectorAll(`col[data-col-index=\"${i}\"]`).forEach(n=>n.style.width=`${px}px`); if(save){columnWidths[c.key]=px; saveColumnWidths();} updateBoardWidth(); }"
        new_set_width = "function setColumnWidth(i,w,save=true){ const c=columns[i]; if(!c)return; const px=stockboardViewMode==='mobile'?columnWidth(i):clampWidth(i,w); document.querySelectorAll(`col[data-col-index=\"${i}\"]`).forEach(n=>n.style.width=`${px}px`); if(save&&stockboardViewMode!=='mobile'){columnWidths[c.key]=px; saveColumnWidths();} updateBoardWidth(); }"
        if old_set_width not in patched:
            raise RuntimeError("StockBoard setColumnWidth anchor not found")
        patched = patched.replace(old_set_width, new_set_width, 1)

        patched = patched.replace(
            "function maybeInitialAutoFit(){ if(firstAutoFitDone)return;",
            "function maybeInitialAutoFit(){ if(stockboardViewMode==='mobile')return; if(firstAutoFitDone)return;",
            1,
        )

        fast_cells_anchor = "const priceCell = tr.cells && tr.cells[4];\n      const rateCell = tr.cells && tr.cells[5];"
        fast_cells_replacement = "const priceCell = stockboardViewMode==='mobile' ? null : (tr.cells && tr.cells[4]);\n      const rateCell = tr.cells && tr.cells[stockboardViewMode==='mobile'?4:5];"
        if fast_cells_anchor not in patched:
            raise RuntimeError("StockBoard fast price cell anchor not found")
        patched = patched.replace(fast_cells_anchor, fast_cells_replacement, 1)

        row_anchor = "  function rowHtml(raw){"
        if row_anchor not in patched:
            raise RuntimeError("StockBoard rowHtml anchor not found")
        patched = patched.replace(row_anchor, "  function desktopRowHtml(raw){", 1)

        render_table_anchor = "  function renderTable(table,rows,empty){"
        if render_table_anchor not in patched:
            raise RuntimeError("StockBoard renderTable anchor not found")
        mobile_row = r'''
  function mobileRowHtml(raw){
    const r=deriveClientFields(raw);
    const code=String(r.stock_code||'');
    const rate=Number(r.change_rate);
    const age=Number(r.price_age_sec);
    const sel=code&&code===selectedCode;
    const cls=`data-row ${sel?'selected-row ':''}${age>3?'stale ':''}`.trim();
    const inst=numeric(r.execution_strength);
    const rateText=Number.isFinite(rate)?`${rate>0?'+':''}${rate.toFixed(2)}%`:'-';
    const amountRatioText=fmtRatio(r.amount_ratio);
    const programText=fmtNum(r.program_net,0);
    return `<tr tabindex="0" data-code="${escapeHtml(code)}" class="${cls}">
      <td class="center${cellFlashClass(code,'rank',r.rank)}">${escapeHtml(r.rank??'-')}</td>
      <td class="center ${clsSigned(r.rank_change)}${cellFlashClass(code,'rank_change',r.rank_change)}">${fmtRankChange(r.rank_change)}</td>
      <td class="center momentum-grade-cell${cellFlashClass(code,'grade',JSON.stringify(r.momentum_badges||[])+'|'+(r.candidate_grade_text||r.grade_text||r.grade||r.candidate_grade||'-'))}">${momentumGradeHtml(r)}</td>
      <td class="stock-name-copy${cellFlashClass(code,'stock_name',r.stock_name||code)}">${escapeHtml(r.stock_name||code)}</td>
      <td class="num ${clsSigned(rate)}${cellFlashClass(code,'change_rate',r.change_rate)}">${rateText}</td>
      <td class="num ${clsRatio(r.amount_ratio)}${cellFlashClass(code,'amount_ratio',r.amount_ratio)}">${amountRatioText}</td>
      <td class="num ${clsStrength(inst)}${cellFlashClass(code,'execution_strength',inst)}">${fmtStrength(inst)}</td>
      <td class="num ${clsSigned(r.program_net)}${cellFlashClass(code,'program_net',r.program_net)}">${programText}</td>
    </tr>`;
  }
  function rowHtml(raw){
    return stockboardViewMode==='mobile' ? mobileRowHtml(raw) : desktopRowHtml(raw);
  }
'''
        patched = patched.replace(render_table_anchor, mobile_row + render_table_anchor, 1)

        if config["mobile_behavior"]["disable_hts_clipboard_link"]:
            hts_anchor = "if(!copyOnly)sendHtsCommand(text);"
            if hts_anchor not in patched:
                raise RuntimeError("StockBoard HTS link anchor not found")
            patched = patched.replace(
                hts_anchor,
                "if(!copyOnly&&stockboardViewMode!=='mobile')sendHtsCommand(text);",
                1,
            )

        init_anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
        if init_anchor not in patched:
            raise RuntimeError("StockBoard initialization anchor not found")
        runtime = r'''
  function stockboardSetMobileClass(){
    const mobile=stockboardViewMode==='mobile';
    document.documentElement.classList.toggle('stockboard-mobile',mobile);
    document.documentElement.classList.toggle('stockboard-desktop',!mobile);
    document.body.classList.toggle('stockboard-mobile',mobile);
    document.body.classList.toggle('stockboard-desktop',!mobile);
    if(stockboardViewToggle)stockboardViewToggle.textContent=mobile?'데스크톱 보기':'모바일 보기';
    if(mobile){
      document.querySelectorAll('#topbar a,#topbar button').forEach(element=>{
        const key=`${element.id||''} ${element.className||''} ${element.getAttribute('href')||''} ${element.textContent||''}`.toLowerCase();
        if(key.includes('strategyboard')||key.includes('전략보드'))element.classList.add('stockboard-mobile-strategy-hidden');
      });
    }
  }
  function stockboardSetColumnHidden(table,columnIndex,hidden){
    if(!table||columnIndex<1)return;
    table.querySelectorAll(`tr > *:nth-child(${columnIndex})`).forEach(cell=>{cell.hidden=!!hidden;});
  }
  function stockboardFitTableRight(table,minVisible){
    if(stockboardViewMode!=='mobile'||!table)return;
    const rows=Array.from(table.rows||[]);
    const count=Math.max(0,...rows.map(row=>row.cells.length));
    for(let index=1;index<=count;index++)stockboardSetColumnHidden(table,index,false);
    const container=table.parentElement;
    const available=Math.max(0,Number(container?.clientWidth)||stockboardViewportWidth());
    for(let index=count;index>Math.max(1,minVisible);index--){
      if(table.getBoundingClientRect().width<=available+0.5)break;
      stockboardSetColumnHidden(table,index,true);
    }
  }
  function stockboardFitMarketContext(){
    if(stockboardViewMode!=='mobile')return;
    const market=STOCKBOARD_VIEW_CONFIG.mobile_market||{};
    stockboardFitTableRight(usMarketRow?.closest('table'),Number(market.us_min_visible_columns)||4);
    stockboardFitTableRight(marketSupplyRow?.closest('table'),Number(market.domestic_min_visible_columns)||4);
  }
  const stockboardOriginalRenderContext=renderContext;
  renderContext=function(payload){
    const result=stockboardOriginalRenderContext(payload);
    requestAnimationFrame(stockboardFitMarketContext);
    return result;
  };
  if(stockboardViewToggle){
    stockboardViewToggle.addEventListener('click',()=>{
      sessionStorage.setItem(STOCKBOARD_VIEW_OVERRIDE_KEY,stockboardViewMode==='mobile'?'desktop':'mobile');
      location.reload();
    });
  }
  stockboardSetMobileClass();
  let stockboardPreviousAutomaticMode=stockboardAutomaticMode();
  let stockboardPreviousViewportWidth=stockboardViewportWidth();
  let stockboardResizeTimer=null;
  window.addEventListener('resize',()=>{
    clearTimeout(stockboardResizeTimer);
    stockboardResizeTimer=setTimeout(()=>{
      const width=stockboardViewportWidth();
      const nextAutomaticMode=stockboardAutomaticMode(width);
      const override=sessionStorage.getItem(STOCKBOARD_VIEW_OVERRIDE_KEY);
      if(override&&Math.abs(width-stockboardPreviousViewportWidth)>=24){
        sessionStorage.removeItem(STOCKBOARD_VIEW_OVERRIDE_KEY);
        if(nextAutomaticMode!==stockboardViewMode){location.reload();return;}
      }
      if(nextAutomaticMode!==stockboardPreviousAutomaticMode){
        sessionStorage.removeItem(STOCKBOARD_VIEW_OVERRIDE_KEY);
        location.reload();
        return;
      }
      stockboardPreviousViewportWidth=width;
      stockboardPreviousAutomaticMode=nextAutomaticMode;
      if(stockboardViewMode==='mobile'){
        applyColumnWidths();
        stockboardFitMarketContext();
      }
    },140);
  });
'''
        patched = patched.replace(init_anchor, runtime + "\n" + init_anchor, 1)

        style = f'''
<style id="stockboard-v2-responsive-mobile-view">
  /* {MARKER} */
  html.stockboard-mobile, html.stockboard-mobile body {{ width:100%; min-width:0 !important; max-width:100%; overflow-x:hidden !important; }}
  html.stockboard-mobile .window {{ width:100%; min-width:0 !important; max-width:100%; overflow-x:hidden; }}
  html.stockboard-mobile #topbar {{ width:100%; min-width:0; padding:3px 4px; gap:3px; box-sizing:border-box; }}
  html.stockboard-mobile #topbar .metric-row {{ gap:3px 4px; }}
  html.stockboard-mobile #topbar .title,
  html.stockboard-mobile #ui-zoom-toggle,
  html.stockboard-mobile #column-minimize-toggle,
  html.stockboard-mobile #row-position-toggle,
  html.stockboard-mobile #copy-status,
  html.stockboard-mobile #counts,
  html.stockboard-mobile #latency,
  html.stockboard-mobile #throughput,
  html.stockboard-mobile #collector-metrics,
  html.stockboard-mobile #worker-metrics,
  html.stockboard-mobile #lag-metrics,
  html.stockboard-mobile #render-metrics,
  html.stockboard-mobile #metric-mode-status,
  html.stockboard-mobile #topbar .small,
  html.stockboard-mobile .stockboard-mobile-strategy-hidden,
  html.stockboard-mobile #topbar a[href*="strategyboard" i],
  html.stockboard-mobile #topbar [id*="strategyboard" i],
  html.stockboard-mobile #topbar [class*="strategyboard" i] {{ display:none !important; }}
  html.stockboard-mobile #topbar .metric-row:has(#counts),
  html.stockboard-mobile #topbar .metric-row:has(#lag-metrics) {{ display:none !important; }}
  html.stockboard-mobile #clock,
  html.stockboard-mobile #status,
  html.stockboard-mobile #stockboard-view-toggle,
  html.stockboard-mobile #candidate-model-selector {{ font-size:11px; }}
  html.stockboard-mobile #momentum-alert-strip {{ order:3; height:21px; padding:1px 4px; gap:4px; }}
  html.stockboard-mobile .momentum-alert-item:nth-of-type(n+3) {{ display:none; }}
  html.stockboard-mobile .context-panel.market-overview-v2 {{ display:block; width:100%; min-width:0; padding:0; overflow:hidden; }}
  html.stockboard-mobile .v2-us-grid,
  html.stockboard-mobile .v2-market-grid {{ width:max-content !important; min-width:0 !important; max-width:none !important; table-layout:auto !important; }}
  html.stockboard-mobile .v2-us-grid td,
  html.stockboard-mobile .v2-market-grid th,
  html.stockboard-mobile .v2-market-grid td {{ padding:1px 3px; font-size:10px; }}
  html.stockboard-mobile .v2-market-graphic-row {{ display:flex; flex-direction:column; width:100%; min-width:0; overflow:hidden; }}
  html.stockboard-mobile .v2-market-distribution {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); width:100%; min-width:0; padding:2px 0; }}
  html.stockboard-mobile .v2-market-dist-item,
  html.stockboard-mobile .v2-market-dist-item:nth-child(3),
  html.stockboard-mobile .v2-market-dist-item:nth-child(4) {{ width:100%; min-width:0; margin:0; }}
  html.stockboard-mobile .v2-subject-flow-chart {{ width:92%; max-width:150px; height:52px; flex-basis:auto; }}
  html.stockboard-mobile .v2-donut-chart {{ width:52px; height:52px; flex:0 0 52px; }}
  html.stockboard-mobile .v2-donut-chart::after {{ inset:17px; }}
  html.stockboard-mobile table.board {{ width:100vw !important; min-width:100vw !important; max-width:100vw !important; table-layout:fixed; }}
  html.stockboard-mobile .board th,
  html.stockboard-mobile .board td {{ height:22px; padding:1px 2px; font-size:9.5px; text-overflow:ellipsis; }}
  html.stockboard-mobile .board th {{ font-size:9px; }}
  html.stockboard-mobile .board .column-resizer {{ display:none !important; }}
  html.stockboard-mobile .board td.stock-name-copy {{ padding-left:3px; text-decoration:none; }}
  html.stockboard-mobile .grade,
  html.stockboard-mobile .momentum-grade-stack {{ min-width:27px; }}
  html.stockboard-mobile .momentum-grade-badge {{ min-width:27px; padding:0 2px; font-size:9px; }}
  html.stockboard-mobile .section {{ width:100%; min-width:0; padding:2px 4px; box-sizing:border-box; font-size:11px; }}
  html.stockboard-mobile #sbv2-horizontal-scroll-spacer {{ display:none !important; width:0 !important; }}
</style>
'''
        if "</head>" not in patched:
            raise RuntimeError("StockBoard head closing tag not found")
        patched = patched.replace("</head>", style + "</head>", 1)
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._responsive_mobile_view_installed = True
