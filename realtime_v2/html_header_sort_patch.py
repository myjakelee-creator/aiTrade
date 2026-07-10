from __future__ import annotations

import re
import traceback
from http import HTTPStatus
from pathlib import Path

MARKER = "STOCKBOARD_V2_HEADER_SORT_WITH_LOCK_20260710"
CONNECTION_MARKER = "STOCKBOARD_V2_CONNECTION_HEALTH_BADGE_20260710"
RENDER_DIET_MARKER = "STOCKBOARD_V2_RENDER_DIET_20260710"
MANUAL_SORT_KEY = "stockboard.v2.headerSortActive.v1"


def apply_header_sort_patch(html: str) -> str:
    if MARKER in html:
        return html

    html = re.sub(
        r"const key=th\.dataset\.sortKey;\s*sortState=",
        "const key=th.dataset.sortKey; window.__sbv2HeaderSortActive=true; try{localStorage.setItem('stockboard.v2.headerSortActive.v1','1');}catch(_e){} sortState=",
        html,
        count=1,
    )

    old = (
        "const raw=Array.isArray(payload.rows)?payload.rows:[],"
        "displayOrderPaused=!!(payload.display_order&&payload.display_order.paused),"
        "ranked=displayOrderPaused?raw:byCandidateScore(raw),"
        "focusRows=displayOrderPaused?ranked.slice(0,20):sortedRows(ranked.slice(0,20)),"
        "poolRows=displayOrderPaused?ranked.slice(20):sortedRows(ranked.slice(20)),"
        "selectedRows="
    )
    new = (
        "const raw=Array.isArray(payload.rows)?payload.rows:[],"
        "displayOrderPaused=!!(payload.display_order&&payload.display_order.paused),"
        "ranked=displayOrderPaused?raw:byCandidateScore(raw),"
        "__sbv2ClientSort=__sbv2ClientSortActive(displayOrderPaused),"
        "focusBase=ranked.slice(0,20),poolBase=ranked.slice(20),"
        "focusRows=__sbv2ClientSort?sortedRows(focusBase):focusBase,"
        "poolRows=__sbv2ClientSort?sortedRows(poolBase):poolBase,"
        "selectedRows="
    )
    html = html.replace(old, new, 1)

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = f'''
  /* {MARKER} */
  window.__sbv2HeaderSortActive = window.__sbv2HeaderSortActive || false;
  function __sbv2ClientSortActive(displayOrderPaused){{
    if(!displayOrderPaused) return true;
    try{{
      return !!window.__sbv2HeaderSortActive || localStorage.getItem('{MANUAL_SORT_KEY}') === '1';
    }}catch(_e){{
      return !!window.__sbv2HeaderSortActive;
    }}
  }}
'''
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_connection_health_patch(html: str) -> str:
    if CONNECTION_MARKER in html:
        return html

    old = "statusEl.textContent=phase?`연결 OK · ${phase}`:'연결 OK';statusEl.className=mode==='stream'?'badge ok':'badge bad';"
    html = html.replace(old, "__sbv2UpdateConnectionHealth(payload,mode,phase);", 1)

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = f'''
  /* {CONNECTION_MARKER} */
  function __sbv2SecondsSinceIso(value){{
    const parsed = Date.parse(value || '');
    return Number.isFinite(parsed) ? Math.max(0, (Date.now() - parsed) / 1000) : null;
  }}
  function __sbv2UpdateConnectionHealth(payload, mode, phase){{
    const st = (payload && payload.status) || {{}};
    const collector = st.collector_status || {{}};
    const sender = collector.sender_stats || {{}};
    const providerStarted = collector.provider_started === true;
    const registered = Number(collector.registered_count || 0);
    const senderConnected = sender.connected === true;
    const eventCount = Number(st.event_count || 0);
    const tradeCount = Number(st.trade_count || 0);
    const lastAge = __sbv2SecondsSinceIso(st.last_event_at || collector.ts || payload.ts);
    const phaseText = phase ? ` · ${{phase}}` : '';
    let label = '';
    let cls = 'badge bad';
    if(!providerStarted || registered <= 0){{
      label = `연결 대기${{phaseText}}`;
      cls = 'badge bad';
    }}else if(!senderConnected){{
      label = `collector 끊김${{phaseText}}`;
      cls = 'badge bad';
    }}else if(lastAge !== null && lastAge > 8){{
      label = `연결 지연 ${{lastAge.toFixed(1)}}s${{phaseText}}`;
      cls = 'badge warn';
    }}else if(eventCount <= 0 && tradeCount <= 0){{
      label = `수신 대기${{phaseText}}`;
      cls = 'badge warn';
    }}else{{
      label = `연결 OK${{phaseText}}`;
      cls = mode === 'stream' ? 'badge ok' : 'badge warn';
    }}
    statusEl.textContent = label;
    statusEl.className = cls;
  }}
'''
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_render_diet_patch(html: str) -> str:
    if RENDER_DIET_MARKER in html:
        return html

    # Prefer the new render-diet interval even if a previous safety patch already
    # replaced the raw 250ms constant with __sbv2PoolIntervalMs().
    html = html.replace(
        "now-lastPoolRenderAt>=__sbv2PoolIntervalMs()",
        "now-lastPoolRenderAt>=__sbv2RenderDietPoolIntervalMs()",
        1,
    )
    html = html.replace(
        "now-lastPoolRenderAt>=250",
        "now-lastPoolRenderAt>=__sbv2RenderDietPoolIntervalMs()",
        1,
    )

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __RENDER_DIET_MARKER__ */
  const __sbv2RenderDiet = {
    flashMap: new Map(),
    pendingArgs: null,
    frame: 0,
    lastFocusAt: 0,
    lastDailyLockAt: 0,
    dailyLockScheduled: false,
    lastBoardWidthAt: 0,
    boardWidthScheduled: false
  };

  function __sbv2RenderDietPoolIntervalMs(){
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    const regularStart = 9 * 60;
    const phase = String(lastPayload?.market_session?.phase || lastPayload?.status?.market_phase || '').toLowerCase();
    if(minutes >= regularStart && minutes < regularStart + 10) return 1800;
    if(phase.includes('after')) return 1200;
    return 900;
  }

  function __sbv2RenderDietModelRank(row, fallback){
    const keys = ['model_rank','funnel_rank','pool_rank','rank'];
    for(const key of keys){
      const n = Number(row && row[key]);
      if(Number.isFinite(n) && n > 0) return n;
    }
    return fallback;
  }

  function __sbv2RenderDietPrepareFlashMap(table, rows){
    if(!Array.isArray(rows)) return;
    const selected = table === selectedBoardEl;
    const focus = table === focusBoardEl;
    const pool = table === poolBoardEl;
    rows.forEach((row, index) => {
      const code = String(row?.stock_code || '');
      if(!/^\d{6}$/.test(code)) return;
      let allow = false;
      if(selected || focus){
        allow = true;
      }else if(pool){
        const rank = __sbv2RenderDietModelRank(row, index + 21);
        allow = rank >= 21 && rank <= 50;
      }
      if(allow) __sbv2RenderDiet.flashMap.set(code, true);
      else if(!__sbv2RenderDiet.flashMap.has(code)) __sbv2RenderDiet.flashMap.set(code, false);
    });
  }

  const __sbv2OriginalRenderTable = renderTable;
  renderTable = function(table, rows, empty){
    __sbv2RenderDietPrepareFlashMap(table, rows);
    return __sbv2OriginalRenderTable.apply(this, arguments);
  };

  const __sbv2OriginalCellFlashClass = cellFlashClass;
  cellFlashClass = function(code, key, value){
    const stockCode = String(code || '').trim();
    const normalized = String(value ?? '');
    const cacheKey = `${stockCode}|${key}`;
    const keyAllowed = key === 'price' || key === 'change_rate';
    const laneAllowed = __sbv2RenderDiet.flashMap.get(stockCode) === true;
    if(!keyAllowed || !laneAllowed){
      if(stockCode && key) previousCellValues.set(cacheKey, normalized);
      return '';
    }
    return __sbv2OriginalCellFlashClass.apply(this, arguments);
  };

  if(typeof lockDailyCandleRuntime === 'function'){
    const __sbv2OriginalDailyLock = lockDailyCandleRuntime;
    lockDailyCandleRuntime = function(force=false){
      const now = performance.now();
      if(force === true || now - __sbv2RenderDiet.lastDailyLockAt >= 1000){
        __sbv2RenderDiet.lastDailyLockAt = now;
        return __sbv2OriginalDailyLock.apply(this, arguments);
      }
      if(!__sbv2RenderDiet.dailyLockScheduled){
        __sbv2RenderDiet.dailyLockScheduled = true;
        setTimeout(() => {
          __sbv2RenderDiet.dailyLockScheduled = false;
          __sbv2RenderDiet.lastDailyLockAt = performance.now();
          __sbv2OriginalDailyLock(true);
        }, 1000);
      }
    };
  }

  if(typeof updateBoardWidth === 'function'){
    const __sbv2OriginalUpdateBoardWidth = updateBoardWidth;
    updateBoardWidth = function(force=false){
      const now = performance.now();
      if(force === true || resizeState || now - __sbv2RenderDiet.lastBoardWidthAt >= 750){
        __sbv2RenderDiet.lastBoardWidthAt = now;
        return __sbv2OriginalUpdateBoardWidth.apply(this, arguments);
      }
      if(!__sbv2RenderDiet.boardWidthScheduled){
        __sbv2RenderDiet.boardWidthScheduled = true;
        setTimeout(() => {
          __sbv2RenderDiet.boardWidthScheduled = false;
          __sbv2RenderDiet.lastBoardWidthAt = performance.now();
          __sbv2OriginalUpdateBoardWidth(true);
        }, 750);
      }
    };
  }

  const __sbv2OriginalFocusSelectedRow = focusSelectedRow;
  focusSelectedRow = function(preventScroll=true){
    if(!selectedCode) return;
    const active = document.activeElement;
    if(active && active.dataset && active.dataset.code === selectedCode) return;
    const now = performance.now();
    if(now - __sbv2RenderDiet.lastFocusAt < 250) return;
    __sbv2RenderDiet.lastFocusAt = now;
    return __sbv2OriginalFocusSelectedRow.call(this, preventScroll);
  };

  const __sbv2OriginalRenderDietRender = render;
  function __sbv2RunRenderDietFrame(){
    __sbv2RenderDiet.frame = 0;
    const args = __sbv2RenderDiet.pendingArgs;
    __sbv2RenderDiet.pendingArgs = null;
    if(!args) return;
    __sbv2RenderDiet.flashMap.clear();
    return __sbv2OriginalRenderDietRender.apply(window, args);
  }

  render = function(payload, mode='stream', opt={}){
    __sbv2RenderDiet.pendingArgs = [payload, mode, opt || {}];
    if(__sbv2RenderDiet.frame) return;
    __sbv2RenderDiet.frame = requestAnimationFrame(__sbv2RunRenderDietFrame);
  };
'''.replace("__RENDER_DIET_MARKER__", RENDER_DIET_MARKER)

    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def install(base, large_module) -> None:
    handler = base.WebHandler
    if getattr(handler, "_stockboard_header_sort_patch_installed", False):
        return

    original_do_get = handler.do_GET

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path in {"/", "/v2", "/stockboard_v2.html"}:
            try:
                html_path = Path(base.ROOT) / "docs" / "stockboard_v2.html"
                html = html_path.read_text(encoding="utf-8-sig")
                html = large_module._five_min_strength_display_patch(html)
                html = large_module._strip_noisy_tooltips_patch(html)
                html = large_module._ui_safety_patch(html)
                html = apply_header_sort_patch(html)
                html = apply_connection_health_patch(html)
                html = apply_render_diet_patch(html)
                body = html.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as error:
                try:
                    runtime = Path(base.RUNTIME_DIR)
                    runtime.mkdir(parents=True, exist_ok=True)
                    (runtime / "html_header_sort_patch_error.txt").write_text(
                        f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        return original_do_get(self)

    handler.do_GET = patched_do_get
    handler._stockboard_header_sort_patch_installed = True
