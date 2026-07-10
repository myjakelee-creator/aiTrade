from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import (
    LARGE_TRADE_THRESHOLD_KRW,
    normalize_code,
    now_text,
    to_int,
    to_number,
)

# Importing worker64_guarded applies the normal guarded StockBoard v2 patches.
guarded = importlib.import_module("realtime_v2.worker64_guarded")
base = guarded.base

# Persist source/timestamp metadata together with the existing large_trade_* totals.
base.DAILY_PERSIST_KEYS = tuple(
    dict.fromkeys(
        (
            *base.DAILY_PERSIST_KEYS,
            "large_trade_source",
            "large_trade_threshold_krw",
            "large_trade_updated_at",
        )
    )
)


def _large_trade_deltas(event: dict[str, Any]) -> dict[str, Any]:
    values = base.merged_event_values(event)
    return {
        "buy_count": to_int(values.get("collector_large_trade_buy_count_delta")) or 0,
        "sell_count": to_int(values.get("collector_large_trade_sell_count_delta")) or 0,
        "buy_sum_eok": to_number(values.get("collector_large_trade_buy_sum_eok_delta")) or 0.0,
        "sell_sum_eok": to_number(values.get("collector_large_trade_sell_sum_eok_delta")) or 0.0,
        "values": values,
    }


def _has_large_trade_delta(delta: dict[str, Any]) -> bool:
    return any(
        value not in (0, 0.0, None)
        for value in (
            delta.get("buy_count"),
            delta.get("sell_count"),
            delta.get("buy_sum_eok"),
            delta.get("sell_sum_eok"),
        )
    )


def _suppress_single_trade_large_count(event: dict[str, Any]) -> dict[str, Any]:
    """Prevent the wrapped worker from also counting the coalesced last tick.

    The collector delta already represents every 50ms-window large trade.  The
    base guarded worker would otherwise add the last coalesced trade one more
    time, so the signed single-trade quantity is suppressed only for the wrapped
    large-trade fallback.  Collector buy/sell flow remains intact for strength.
    """

    next_event = deepcopy(event)
    kwargs = next_event.get("kwargs") if isinstance(next_event.get("kwargs"), dict) else None
    values = next_event.get("values") if isinstance(next_event.get("values"), dict) else None
    for container in (kwargs, values):
        if not isinstance(container, dict):
            continue
        container["trade_qty"] = 0
        container["cntg_vol"] = 0
        raw = container.get("raw")
        if isinstance(raw, dict):
            raw["trade_qty_raw"] = 0
            raw["cntg_vol"] = 0
    return next_event


def _apply_large_trade_delta(state, code: str, quote: dict[str, Any], delta: dict[str, Any], received_at: Any) -> None:
    buy_delta = int(delta.get("buy_count") or 0)
    sell_delta = int(delta.get("sell_count") or 0)
    buy_sum_delta = float(delta.get("buy_sum_eok") or 0.0)
    sell_sum_delta = float(delta.get("sell_sum_eok") or 0.0)

    quote["large_trade_buy_count"] = int(quote.get("large_trade_buy_count") or 0) + buy_delta
    quote["large_trade_sell_count"] = int(quote.get("large_trade_sell_count") or 0) + sell_delta
    quote["large_trade_buy_sum_eok"] = round(float(quote.get("large_trade_buy_sum_eok") or 0.0) + buy_sum_delta, 4)
    quote["large_trade_sell_sum_eok"] = round(float(quote.get("large_trade_sell_sum_eok") or 0.0) + sell_sum_delta, 4)
    quote["large_trade_net_count"] = int(quote.get("large_trade_buy_count") or 0) - int(quote.get("large_trade_sell_count") or 0)
    quote["large_trade_net_sum_eok"] = round(
        float(quote.get("large_trade_buy_sum_eok") or 0.0) - float(quote.get("large_trade_sell_sum_eok") or 0.0),
        4,
    )
    quote["large_trade_source"] = "collector_aggregate"
    quote["large_trade_threshold_krw"] = LARGE_TRADE_THRESHOLD_KRW
    quote["large_trade_updated_at"] = received_at or now_text()

    daily_entry = state.daily_values_by_code.setdefault(code, {})
    for key in base.DAILY_PERSIST_KEYS:
        if key.startswith("large_trade_"):
            daily_entry[key] = quote.get(key)

    state.status["large_trade_last_code"] = code
    state.status["large_trade_last_at"] = quote.get("large_trade_updated_at")
    state.status["large_trade_last_buy_delta"] = buy_delta
    state.status["large_trade_last_sell_delta"] = sell_delta
    state.status["large_trade_last_source"] = "collector_aggregate"
    state._mark_daily_dirty()


_original_apply_trade = base.State._apply_trade


def _patched_apply_trade(self, event: dict[str, Any]) -> None:
    delta = _large_trade_deltas(event)
    if not _has_large_trade_delta(delta):
        return _original_apply_trade(self, event)

    values = delta.get("values") if isinstance(delta.get("values"), dict) else base.merged_event_values(event)
    code = normalize_code(
        event.get("stock_code")
        or event.get("received_code")
        or values.get("stock_code")
        or values.get("normalized_code")
        or values.get("received_code")
    )
    if not code:
        return _original_apply_trade(self, event)

    before_dropped = int(self.status.get("dropped_trade_count") or 0)
    _original_apply_trade(self, _suppress_single_trade_large_count(event))
    after_dropped = int(self.status.get("dropped_trade_count") or 0)
    if after_dropped > before_dropped:
        return

    quote = self.quotes.get(code) or self._quote(code)
    received_at = (
        event.get("ts")
        or values.get("price_received_at")
        or values.get("trade_received_at")
        or values.get("received_at")
        or now_text()
    )
    _apply_large_trade_delta(self, code, quote, delta, received_at)


base.State._apply_trade = _patched_apply_trade


_original_do_get = base.WebHandler.do_GET


def _large_trade_title_patch(html: str) -> str:
    if "function largeTradeTitle(" not in html:
        anchor = "  function rowTitle(r){return[`코드: ${r.stock_code||'-'}`,`행 클릭 즉시 S1 + HTS 연동`,`포커스 후 ↑/↓ 이동`,`전일 거래대금: ${fmtNum(r.prev_trade_value_eok,0)}억`,`대금비: ${fmtRatio(r.amount_ratio)}`,`등급: ${r.candidate_grade_text||r.grade_text||'-'}`].join('\\n');}"
        helper = anchor + "\n  function largeTradeTitle(r){return `5천만원↑ 대량체결: 매수 ${fmtNum(r.large_trade_buy_count,0)} / 매도 ${fmtNum(r.large_trade_sell_count,0)} / 순 ${fmtNum(r.large_trade_net_count,0)}건\\n금액: 순 ${fmtNum(r.large_trade_net_sum_eok,1)}억`; }"
        html = html.replace(anchor, helper, 1)

    old = "<td class=\"num ${clsSigned(r.large_trade_net_count)}${cellFlashClass(code,'large_trade_net_count',r.large_trade_net_count)}\">${largeText}</td>"
    new = "<td class=\"num ${clsSigned(r.large_trade_net_count)}${cellFlashClass(code,'large_trade_net_count',r.large_trade_net_count)}\" title=\"${escapeHtml(largeTradeTitle(r))}\">${largeText}</td>"
    return html.replace(old, new, 1)


def _speed_render_patch(html: str) -> str:
    """Patch the served v2 HTML to reduce DOM churn without adding calculations.

    The browser remains display-only: this patch explicitly disables the legacy
    client-side field synthesis path and only renders fields already present in
    worker rows.
    """

    marker = "STOCKBOARD_V2_LARGE_SPEED_PATCH"
    if marker in html:
        return html

    # Top20/S1 stay hot.  Top300 Pool is a watchlist and should not force a
    # 300-row DOM pass four times per second during the opening burst.
    html = html.replace(
        "const now=performance.now(),shouldPool=opt.forcePool||now-lastPoolRenderAt>=250,",
        "const now=performance.now(),shouldPool=opt.forcePool||now-lastPoolRenderAt>=1000,",
    )

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r"""
  /*
   * STOCKBOARD_V2_LARGE_SPEED_PATCH
   * Display-only rendering patch for the large-trade wrapper.
   * Do not synthesize trading fields in HTML. Worker rows are the source of truth.
   */
  deriveClientFields = function(row){
    return row && typeof row === 'object' ? {...row} : {};
  };

  const __largeSpeedRenderCache = new WeakMap();

  function __largeSpeedMetricModeSignature(){
    try {
      return Array.from(metricKeys || [])
        .map(key => `${key}:${metricModes[key] || 'number'}`)
        .join(',');
    } catch(_e) {
      return '';
    }
  }

  function __largeSpeedStableValue(value){
    if(value === null || value === undefined) return '';
    if(typeof value === 'object'){
      try { return JSON.stringify(value); } catch(_e) { return String(value); }
    }
    return String(value);
  }

  function __largeSpeedRowSignature(row){
    const r = row && typeof row === 'object' ? row : {};
    const code = String(r.stock_code || '');
    const selected = code && code === selectedCode ? '1' : '0';
    const keys = [
      'stock_code','rank','rank_change','candidate_grade_text','grade_text','grade','candidate_grade',
      'grade_score','candidate_score','score_percent','stock_name','price','change_rate',
      'trade_value_eok','prev_trade_value_eok','amount_ratio','ohlc','realtime_ohlc','display_ohlc',
      'bid_ask_ratio','execution_strength','strength_1m','program_net','large_trade_net_count',
      'large_trade_buy_count','large_trade_sell_count','large_trade_buy_sum_eok','large_trade_sell_sum_eok',
      'large_trade_net_sum_eok','price_age_sec'
    ];
    return `${selected}|${__largeSpeedMetricModeSignature()}|${keys.map(key => __largeSpeedStableValue(r[key])).join('|')}`;
  }

  function __largeSpeedMakeRow(row, options){
    const template = document.createElement('template');
    template.innerHTML = rowHtml(row).trim();
    const next = template.content.firstElementChild;
    if(!next) return null;
    if(options && options.suppressFlash){
      next.querySelectorAll('.cell-flash').forEach(cell => cell.classList.remove('cell-flash'));
    }
    if(options && options.suppressTitle){
      next.removeAttribute('title');
      next.querySelectorAll('[title]').forEach(cell => cell.removeAttribute('title'));
    }
    return next;
  }

  function __largeSpeedCopyRowAttributes(target, source){
    for(const name of target.getAttributeNames()){
      if(!source.hasAttribute(name)) target.removeAttribute(name);
    }
    for(const attr of source.attributes){
      if(target.getAttribute(attr.name) !== attr.value) target.setAttribute(attr.name, attr.value);
    }
  }

  function __largeSpeedPatchRow(existing, next){
    if(!existing || !next) return next || existing;
    __largeSpeedCopyRowAttributes(existing, next);
    const oldCells = Array.from(existing.children);
    const newCells = Array.from(next.children);
    if(oldCells.length !== newCells.length){
      existing.replaceWith(next);
      return next;
    }
    for(let i = 0; i < newCells.length; i += 1){
      if(oldCells[i].outerHTML !== newCells[i].outerHTML){
        oldCells[i].replaceWith(newCells[i].cloneNode(true));
      }
    }
    return existing;
  }

  function __largeSpeedUpdateWindow(order, cache, budget, priorityCodes){
    const size = order.length;
    if(!Number.isFinite(budget) || budget <= 0 || budget >= size){
      return new Set(order);
    }
    const result = new Set(priorityCodes || []);
    const start = Math.max(0, Math.min(size - 1, Number(cache.cursor || 0)));
    for(let offset = 0; offset < budget && offset < size; offset += 1){
      result.add(order[(start + offset) % size]);
    }
    cache.cursor = (start + Math.min(budget, size)) % size;
    return result;
  }

  function __largeSpeedRenderTable(table, rows, empty, options = {}){
    const tb = table && table.tBodies ? table.tBodies[0] : null;
    if(!tb) return;
    const nextRows = Array.isArray(rows) ? rows : [];
    let cache = __largeSpeedRenderCache.get(table);
    if(!cache){
      cache = {nodes:new Map(), signatures:new Map(), order:[], empty:false, sortKey:'', cursor:0};
      __largeSpeedRenderCache.set(table, cache);
    }

    if(!nextRows.length){
      if(!cache.empty){
        tb.innerHTML = `<tr><td colspan="${columns.length}" class="center">${escapeHtml(empty)}</td></tr>`;
        cache.nodes.clear();
        cache.signatures.clear();
        cache.order = [];
        cache.cursor = 0;
        cache.empty = true;
      }
      return;
    }
    cache.empty = false;

    const rowsByCode = new Map();
    const incomingOrder = [];
    nextRows.forEach((row, index) => {
      const code = String(row && row.stock_code || `__row_${index}`);
      if(!rowsByCode.has(code)) incomingOrder.push(code);
      rowsByCode.set(code, row);
    });

    const sortKey = `${sortState.key || ''}/${sortState.dir || ''}`;
    if(options.stableOrder && cache.sortKey !== sortKey){
      cache.order = [];
      cache.cursor = 0;
      cache.sortKey = sortKey;
    }
    if(!options.stableOrder){
      cache.order = incomingOrder.slice();
      cache.cursor = 0;
      cache.sortKey = sortKey;
    } else {
      const stillVisible = new Set(incomingOrder);
      cache.order = cache.order.filter(code => stillVisible.has(code));
      incomingOrder.forEach(code => {
        if(!cache.order.includes(code)) cache.order.push(code);
      });
    }

    const priorityCodes = [];
    if(selectedCode) priorityCodes.push(String(selectedCode));
    const updateBudget = Number(options.maxUpdateRows || 0);
    const updateCodes = __largeSpeedUpdateWindow(cache.order, cache, updateBudget, priorityCodes);
    const desiredNodes = [];
    const used = new Set();

    cache.order.forEach(code => {
      const row = rowsByCode.get(code);
      if(!row) return;
      used.add(code);
      const signature = __largeSpeedRowSignature(row);
      let node = cache.nodes.get(code);
      if(!node || cache.signatures.get(code) !== signature){
        if(!node || updateCodes.has(code)){
          const nextNode = __largeSpeedMakeRow(row, options);
          if(!nextNode) return;
          node = node ? __largeSpeedPatchRow(node, nextNode) : nextNode;
          cache.nodes.set(code, node);
          cache.signatures.set(code, signature);
        }
      }
      if(node) desiredNodes.push(node);
    });

    for(const code of Array.from(cache.nodes.keys())){
      if(!used.has(code)){
        cache.nodes.delete(code);
        cache.signatures.delete(code);
      }
    }

    const currentNodes = Array.from(tb.children);
    const sameDomOrder = currentNodes.length === desiredNodes.length
      && desiredNodes.every((node, index) => currentNodes[index] === node);
    if(!sameDomOrder){
      tb.replaceChildren(...desiredNodes);
    }
  }

  renderTable = function(table, rows, empty){
    if(table === poolBoardEl){
      return __largeSpeedRenderTable(table, rows, empty, {
        stableOrder: true,
        suppressFlash: true,
        suppressTitle: true,
        maxUpdateRows: 50
      });
    }
    return __largeSpeedRenderTable(table, rows, empty, {
      stableOrder: false,
      suppressFlash: false,
      suppressTitle: false,
      maxUpdateRows: 0
    });
  };

  const __largeSpeedRenderSamples = [];
  function __largeSpeedUpdateRenderDiagnostics(){
    if(!renderMetricsEl) return;
    const match = /render\s+([0-9.]+)\s+ms/.exec(String(renderMetricsEl.textContent || ''));
    if(!match) return;
    const latest = Number(match[1]);
    if(!Number.isFinite(latest)) return;
    __largeSpeedRenderSamples.push(latest);
    while(__largeSpeedRenderSamples.length > 120) __largeSpeedRenderSamples.shift();
    const sorted = __largeSpeedRenderSamples.slice().sort((a,b) => a-b);
    const average = __largeSpeedRenderSamples.reduce((sum, value) => sum + value, 0) / __largeSpeedRenderSamples.length;
    const p95 = sorted[Math.max(0, Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * 0.95)))];
    const max = sorted[sorted.length - 1];
    renderMetricsEl.textContent = `render ${latest.toFixed(1)} ms · avg ${average.toFixed(0)} · p95 ${p95.toFixed(0)} · max ${max.toFixed(0)}`;
    renderMetricsEl.title = `최근 ${__largeSpeedRenderSamples.length}회 렌더 기준`;
  }

  let __largeSpeedLastNavigationTable = null;
  const __largeSpeedBaseMoveSelection = typeof moveSelection === 'function' ? moveSelection : null;

  function __largeSpeedIsBoardTable(table){
    return table === selectedBoardEl || table === focusBoardEl || table === poolBoardEl;
  }

  function __largeSpeedNavigationTable(){
    const active = document.activeElement;
    const activeRow = active && active.closest ? active.closest('tr[data-code]') : null;
    const activeTable = activeRow ? activeRow.closest('table') : null;
    if(__largeSpeedIsBoardTable(activeTable)) return activeTable;

    if(selectedCode){
      const selectedRows = Array.from(document.querySelectorAll(`tr[data-code="${selectedCode}"]`));
      for(const row of selectedRows){
        const table = row.closest('table');
        if(__largeSpeedIsBoardTable(table)) return table;
      }
    }

    if(__largeSpeedIsBoardTable(__largeSpeedLastNavigationTable)) return __largeSpeedLastNavigationTable;
    return focusBoardEl || poolBoardEl || selectedBoardEl;
  }

  function __largeSpeedRowsInTable(table){
    const body = table && table.tBodies ? table.tBodies[0] : null;
    return Array.from(body ? body.querySelectorAll('tr[data-code]') : [])
      .filter(row => /^\d{6}$/.test(String(row.dataset.code || '')));
  }

  function __largeSpeedFocusCodeInTable(table, code){
    const row = table && table.querySelector ? table.querySelector(`tbody tr[data-code="${code}"]`) : null;
    if(row && typeof row.focus === 'function'){
      row.focus({preventScroll:true});
      if(typeof row.scrollIntoView === 'function'){
        row.scrollIntoView({block:'nearest', inline:'nearest'});
      }
      return true;
    }
    return false;
  }

  moveSelection = function(delta){
    const table = __largeSpeedNavigationTable();
    const rows = __largeSpeedRowsInTable(table);
    if(!rows.length){
      if(__largeSpeedBaseMoveSelection) return __largeSpeedBaseMoveSelection(delta);
      return;
    }

    const active = document.activeElement;
    const activeRow = active && active.closest ? active.closest('tr[data-code]') : null;
    let index = rows.findIndex(row => row === activeRow || String(row.dataset.code || '') === selectedCode);
    if(index < 0) index = delta > 0 ? -1 : 0;
    const nextRow = rows[(index + delta + rows.length) % rows.length];
    const code = String(nextRow && nextRow.dataset.code || '');
    if(!/^\d{6}$/.test(code)) return;
    __largeSpeedLastNavigationTable = table;
    selectCodeAndLink(code, false, {focus:false});
    requestAnimationFrame(() => __largeSpeedFocusCodeInTable(table, code));
  };

  function __largeSpeedBoardWidth(){
    const cssWidth = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--board-width')) || 0;
    const tableWidths = Array.from(document.querySelectorAll('table.board')).map(table => Math.max(table.scrollWidth || 0, table.getBoundingClientRect().width || 0));
    return Math.max(cssWidth, ...tableWidths, 0);
  }

  function __largeSpeedApplyHorizontalScrollFix(){
    const boardWidth = __largeSpeedBoardWidth();
    const minWidth = Math.ceil(Math.max(window.innerWidth + 180, boardWidth + 240));
    const windowEl = document.querySelector('.window');
    if(windowEl) windowEl.style.minWidth = `${minWidth}px`;
    document.documentElement.style.minWidth = `${minWidth}px`;
    document.body.style.minWidth = `${minWidth}px`;
  }

  const __largeSpeedBaseUpdateBoardWidth = typeof updateBoardWidth === 'function' ? updateBoardWidth : null;
  if(__largeSpeedBaseUpdateBoardWidth){
    updateBoardWidth = function(...args){
      const result = __largeSpeedBaseUpdateBoardWidth.apply(this, args);
      requestAnimationFrame(__largeSpeedApplyHorizontalScrollFix);
      return result;
    };
  }
  window.addEventListener('resize', () => requestAnimationFrame(__largeSpeedApplyHorizontalScrollFix));
  setTimeout(__largeSpeedApplyHorizontalScrollFix, 0);
  setTimeout(__largeSpeedApplyHorizontalScrollFix, 300);

  const __largeSpeedBaseRender = render;
  render = function(...args){
    const result = __largeSpeedBaseRender.apply(this, args);
    __largeSpeedUpdateRenderDiagnostics();
    requestAnimationFrame(__largeSpeedApplyHorizontalScrollFix);
    return result;
  };
"""
    if anchor not in html:
        return html
    return html.replace(anchor, f"{patch}\n{anchor}", 1)


def _patched_do_get(self) -> None:
    parsed = base.urlparse(self.path)
    if parsed.path in {"/", "/v2", "/stockboard_v2.html"}:
        html_path = Path(base.ROOT) / "docs" / "stockboard_v2.html"
        html = html_path.read_text(encoding="utf-8-sig")
        html = _large_trade_title_patch(html)
        html = _speed_render_patch(html)
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return
    return _original_do_get(self)


base.WebHandler.do_GET = _patched_do_get


if __name__ == "__main__":
    raise SystemExit(base.main())
