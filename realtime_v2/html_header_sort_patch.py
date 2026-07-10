from __future__ import annotations

import re
import traceback
from http import HTTPStatus
from pathlib import Path

MARKER = "STOCKBOARD_V2_HEADER_SORT_WITH_LOCK_20260710"
CONNECTION_MARKER = "STOCKBOARD_V2_CONNECTION_HEALTH_BADGE_20260710"
FLASH_SCOPE_MARKER = "STOCKBOARD_V2_FLASH_SCOPE_20260710"
POOL_INTERVAL_MARKER = "STOCKBOARD_V2_POOL_INTERVAL_20260710"
POOL_SIGNATURE_MARKER = "STOCKBOARD_V2_POOL_SIGNATURE_20260710"
WIDTH_THROTTLE_MARKER = "STOCKBOARD_V2_WIDTH_THROTTLE_20260710"
FOCUS_THROTTLE_MARKER = "STOCKBOARD_V2_FOCUS_THROTTLE_20260710"
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
    patch = r'''
  /* __MARKER__ */
  window.__sbv2HeaderSortActive = window.__sbv2HeaderSortActive || false;
  function __sbv2ClientSortActive(displayOrderPaused){
    if(!displayOrderPaused) return true;
    try{
      return !!window.__sbv2HeaderSortActive || localStorage.getItem('__MANUAL_SORT_KEY__') === '1';
    }catch(_e){
      return !!window.__sbv2HeaderSortActive;
    }
  }
'''.replace("__MARKER__", MARKER).replace("__MANUAL_SORT_KEY__", MANUAL_SORT_KEY)
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_connection_health_patch(html: str) -> str:
    if CONNECTION_MARKER in html:
        return html

    old = "statusEl.textContent=phase?`연결 OK · ${phase}`:'연결 OK';statusEl.className=mode==='stream'?'badge ok':'badge bad';"
    html = html.replace(old, "__sbv2UpdateConnectionHealth(payload,mode,phase);", 1)

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __CONNECTION_MARKER__ */
  function __sbv2SecondsSinceIso(value){
    const parsed = Date.parse(value || '');
    return Number.isFinite(parsed) ? Math.max(0, (Date.now() - parsed) / 1000) : null;
  }
  function __sbv2UpdateConnectionHealth(payload, mode, phase){
    const st = (payload && payload.status) || {};
    const collector = st.collector_status || {};
    const sender = collector.sender_stats || {};
    const providerStarted = collector.provider_started === true;
    const registered = Number(collector.registered_count || 0);
    const senderConnected = sender.connected === true;
    const eventCount = Number(st.event_count || 0);
    const tradeCount = Number(st.trade_count || 0);
    const lastAge = __sbv2SecondsSinceIso(st.last_event_at || collector.ts || payload.ts);
    const phaseText = phase ? ` · ${phase}` : '';
    let label = '';
    let cls = 'badge bad';
    if(!providerStarted || registered <= 0){
      label = `연결 대기${phaseText}`;
      cls = 'badge bad';
    }else if(!senderConnected){
      label = `collector 끊김${phaseText}`;
      cls = 'badge bad';
    }else if(lastAge !== null && lastAge > 8){
      label = `연결 지연 ${lastAge.toFixed(1)}s${phaseText}`;
      cls = 'badge warn';
    }else if(eventCount <= 0 && tradeCount <= 0){
      label = `수신 대기${phaseText}`;
      cls = 'badge warn';
    }else{
      label = `연결 OK${phaseText}`;
      cls = mode === 'stream' ? 'badge ok' : 'badge warn';
    }
    statusEl.textContent = label;
    statusEl.className = cls;
  }
'''.replace("__CONNECTION_MARKER__", CONNECTION_MARKER)
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_flash_scope_patch(html: str) -> str:
    if FLASH_SCOPE_MARKER in html:
        return html

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __FLASH_SCOPE_MARKER__ */
  const __sbv2FlashScopeState = { map: new Map() };

  function __sbv2FlashScopeRank(row, fallback){
    const keys = ['model_rank','funnel_rank','pool_rank','rank'];
    for(const key of keys){
      const n = Number(row && row[key]);
      if(Number.isFinite(n) && n > 0) return n;
    }
    return fallback;
  }

  function __sbv2FlashScopePrepare(table, rows){
    if(!Array.isArray(rows)) return;
    const isSelected = table === selectedBoardEl;
    const isFocus = table === focusBoardEl;
    const isPool = table === poolBoardEl;
    rows.forEach((row, index) => {
      const code = String(row && row.stock_code || '');
      if(!/^\d{6}$/.test(code)) return;
      let allow = false;
      if(isSelected || isFocus){
        allow = true;
      }else if(isPool){
        const rank = __sbv2FlashScopeRank(row, index + 21);
        allow = rank >= 21 && rank <= 50;
      }
      __sbv2FlashScopeState.map.set(code, allow);
    });
  }

  if(typeof renderTable === 'function' && typeof cellFlashClass === 'function'){
    const __sbv2OriginalFlashRenderTable = renderTable;
    renderTable = function(table, rows, empty){
      __sbv2FlashScopePrepare(table, rows);
      return __sbv2OriginalFlashRenderTable.apply(this, arguments);
    };

    const __sbv2OriginalFlashClass = cellFlashClass;
    cellFlashClass = function(code, key, value){
      const stockCode = String(code || '').trim();
      const normalized = String(value ?? '');
      const cacheKey = `${stockCode}|${key}`;
      const keyAllowed = key === 'price' || key === 'change_rate';
      const laneAllowed = __sbv2FlashScopeState.map.get(stockCode) === true;
      if(!keyAllowed || !laneAllowed){
        if(stockCode && key) previousCellValues.set(cacheKey, normalized);
        return '';
      }
      return __sbv2OriginalFlashClass.apply(this, arguments);
    };
  }
'''.replace("__FLASH_SCOPE_MARKER__", FLASH_SCOPE_MARKER)

    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_pool_interval_patch(html: str) -> str:
    if POOL_INTERVAL_MARKER in html:
        return html

    html = html.replace(
        "now-lastPoolRenderAt>=__sbv2PoolIntervalMs()",
        "now-lastPoolRenderAt>=__sbv2PoolDietIntervalMs()",
        1,
    )
    html = html.replace(
        "now-lastPoolRenderAt>=250",
        "now-lastPoolRenderAt>=__sbv2PoolDietIntervalMs()",
        1,
    )

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __POOL_INTERVAL_MARKER__ */
  function __sbv2PoolDietIntervalMs(){
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    const phase = String(lastPayload?.market_session?.phase || lastPayload?.status?.market_phase || '').toLowerCase();
    if(minutes >= 9 * 60 && minutes < 9 * 60 + 10) return 2500;
    if(phase.includes('after')) return 2000;
    return 1500;
  }
'''.replace("__POOL_INTERVAL_MARKER__", POOL_INTERVAL_MARKER)
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_pool_signature_patch(html: str) -> str:
    if POOL_SIGNATURE_MARKER in html:
        return html

    old = "if(shouldPool){renderTable(poolBoardEl,poolRows,'Top300 Pool 수신 대기 중입니다.');poolRowCountEl.textContent=`21~${ranked.length} · ${poolRows.length}종목`;lastPoolRenderAt=now;}"
    new = "if(shouldPool){if(__sbv2ShouldRenderPool(poolRows,opt)){renderTable(poolBoardEl,poolRows,'Top300 Pool 수신 대기 중입니다.');poolRowCountEl.textContent=`21~${ranked.length} · ${poolRows.length}종목`;}lastPoolRenderAt=now;}"
    if old not in html:
        return html
    html = html.replace(old, new, 1)

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __POOL_SIGNATURE_MARKER__ */
  const __sbv2PoolSignatureState = { sig: '' };

  function __sbv2PoolSigValue(value){
    return value === undefined || value === null ? '' : String(value);
  }

  function __sbv2PoolOhlcSig(row){
    const o = (row && (row.ohlc || row.realtime_ohlc || row.display_ohlc)) || {};
    return [o.open, o.high, o.low, o.close, o.current].map(__sbv2PoolSigValue).join('/');
  }

  function __sbv2PoolSignature(rows){
    const modeSig = Array.from(metricKeys || []).map(k => `${k}:${metricModes[k] || ''}`).join(',');
    const parts = [selectedCode || '', sortState?.key || '', sortState?.dir || '', modeSig];
    (rows || []).forEach(row => {
      parts.push(
        __sbv2PoolSigValue(row && row.stock_code),
        __sbv2PoolSigValue(row && row.rank),
        __sbv2PoolSigValue(row && row.rank_change),
        __sbv2PoolSigValue(row && (row.candidate_grade_text || row.grade_text || row.grade || row.candidate_grade)),
        __sbv2PoolSigValue(row && row.stock_name),
        __sbv2PoolSigValue(row && row.price),
        __sbv2PoolSigValue(row && row.change_rate),
        __sbv2PoolSigValue(row && row.trade_value_eok),
        __sbv2PoolSigValue(row && row.amount_ratio),
        __sbv2PoolOhlcSig(row),
        __sbv2PoolSigValue(row && row.bid_ask_ratio),
        __sbv2PoolSigValue(row && row.execution_strength),
        __sbv2PoolSigValue(row && row.strength_1m),
        __sbv2PoolSigValue(row && row.program_net),
        __sbv2PoolSigValue(row && row.large_trade_net_count),
        __sbv2PoolSigValue(row && row.price_age_sec)
      );
    });
    return parts.join('|');
  }

  function __sbv2ShouldRenderPool(rows, opt){
    const sig = __sbv2PoolSignature(rows || []);
    if(opt && opt.forcePool){
      __sbv2PoolSignatureState.sig = sig;
      return true;
    }
    if(sig === __sbv2PoolSignatureState.sig) return false;
    __sbv2PoolSignatureState.sig = sig;
    return true;
  }
'''.replace("__POOL_SIGNATURE_MARKER__", POOL_SIGNATURE_MARKER)
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_width_throttle_patch(html: str) -> str:
    if WIDTH_THROTTLE_MARKER in html:
        return html

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __WIDTH_THROTTLE_MARKER__ */
  const __sbv2WidthThrottle = {
    lastBoardWidthAt: 0,
    boardWidthTimer: 0,
    lastDailyLockAt: 0,
    dailyLockTimer: 0
  };

  if(typeof updateBoardWidth === 'function'){
    const __sbv2OriginalUpdateBoardWidth = updateBoardWidth;
    updateBoardWidth = function(force=false){
      const now = performance.now();
      if(force === true || resizeState || now - __sbv2WidthThrottle.lastBoardWidthAt >= 750){
        __sbv2WidthThrottle.lastBoardWidthAt = now;
        return __sbv2OriginalUpdateBoardWidth.apply(this, arguments);
      }
      if(!__sbv2WidthThrottle.boardWidthTimer){
        __sbv2WidthThrottle.boardWidthTimer = setTimeout(() => {
          __sbv2WidthThrottle.boardWidthTimer = 0;
          __sbv2WidthThrottle.lastBoardWidthAt = performance.now();
          __sbv2OriginalUpdateBoardWidth();
        }, 750);
      }
    };
  }

  if(typeof lockDailyCandleRuntime === 'function'){
    const __sbv2OriginalDailyLock = lockDailyCandleRuntime;
    lockDailyCandleRuntime = function(force=false){
      const now = performance.now();
      if(force === true || now - __sbv2WidthThrottle.lastDailyLockAt >= 1000){
        __sbv2WidthThrottle.lastDailyLockAt = now;
        return __sbv2OriginalDailyLock.apply(this, arguments);
      }
      if(!__sbv2WidthThrottle.dailyLockTimer){
        __sbv2WidthThrottle.dailyLockTimer = setTimeout(() => {
          __sbv2WidthThrottle.dailyLockTimer = 0;
          __sbv2WidthThrottle.lastDailyLockAt = performance.now();
          __sbv2OriginalDailyLock();
        }, 1000);
      }
    };
  }
'''.replace("__WIDTH_THROTTLE_MARKER__", WIDTH_THROTTLE_MARKER)

    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def apply_focus_throttle_patch(html: str) -> str:
    if FOCUS_THROTTLE_MARKER in html:
        return html

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* __FOCUS_THROTTLE_MARKER__ */
  const __sbv2FocusThrottle = { lastAt: 0 };

  if(typeof focusSelectedRow === 'function'){
    const __sbv2OriginalFocusSelectedRow = focusSelectedRow;
    focusSelectedRow = function(preventScroll=true){
      if(!selectedCode) return;
      const active = document.activeElement;
      if(active && active.dataset && active.dataset.code === selectedCode) return;
      const now = performance.now();
      if(now - __sbv2FocusThrottle.lastAt < 250) return;
      __sbv2FocusThrottle.lastAt = now;
      return __sbv2OriginalFocusSelectedRow.call(this, preventScroll);
    };
  }
'''.replace("__FOCUS_THROTTLE_MARKER__", FOCUS_THROTTLE_MARKER)

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
                html = apply_flash_scope_patch(html)
                html = apply_pool_interval_patch(html)
                html = apply_pool_signature_patch(html)
                html = apply_width_throttle_patch(html)
                html = apply_focus_throttle_patch(html)
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