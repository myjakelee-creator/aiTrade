from __future__ import annotations

import importlib
import re
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

# Persist large-trade metadata and the last valid opt10046 strength values.
base.DAILY_PERSIST_KEYS = tuple(
    dict.fromkeys(
        (
            *base.DAILY_PERSIST_KEYS,
            "large_trade_source",
            "large_trade_threshold_krw",
            "large_trade_updated_at",
            "strength_5m",
            "strength_20m",
            "strength_60m",
            "strength_source",
            "strength_snapshot_at",
            "strength_status",
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
    """Prevent the wrapped worker from also counting the coalesced last tick."""

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


def _apply_large_trade_delta(
    state,
    code: str,
    quote: dict[str, Any],
    delta: dict[str, Any],
    received_at: Any,
) -> None:
    buy_delta = int(delta.get("buy_count") or 0)
    sell_delta = int(delta.get("sell_count") or 0)
    buy_sum_delta = float(delta.get("buy_sum_eok") or 0.0)
    sell_sum_delta = float(delta.get("sell_sum_eok") or 0.0)

    quote["large_trade_buy_count"] = int(quote.get("large_trade_buy_count") or 0) + buy_delta
    quote["large_trade_sell_count"] = int(quote.get("large_trade_sell_count") or 0) + sell_delta
    quote["large_trade_buy_sum_eok"] = round(
        float(quote.get("large_trade_buy_sum_eok") or 0.0) + buy_sum_delta, 4
    )
    quote["large_trade_sell_sum_eok"] = round(
        float(quote.get("large_trade_sell_sum_eok") or 0.0) + sell_sum_delta, 4
    )
    quote["large_trade_net_count"] = int(quote.get("large_trade_buy_count") or 0) - int(
        quote.get("large_trade_sell_count") or 0
    )
    quote["large_trade_net_sum_eok"] = round(
        float(quote.get("large_trade_buy_sum_eok") or 0.0)
        - float(quote.get("large_trade_sell_sum_eok") or 0.0),
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

    values = (
        delta.get("values")
        if isinstance(delta.get("values"), dict)
        else base.merged_event_values(event)
    )
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


def _five_min_strength_display_patch(html: str) -> str:
    """Replace the legacy one-minute display with worker-supplied strength_5m.

    No strength or OHLC calculation is performed in the browser. The worker row is
    the only source of the displayed value.
    """

    marker = "STOCKBOARD_V2_STRENGTH5M_DISPLAY_20260710"
    if marker in html:
        return html

    html = html.replace("잔량비/순간강도/1분강도", "잔량비/순간강도/5분강도")
    html = html.replace(
        "const metricKeys = new Set(['bid_ask_ratio', 'execution_strength', 'strength_1m']);",
        "const metricKeys = new Set(['bid_ask_ratio', 'execution_strength', 'strength_5m']);",
    )
    html = html.replace(
        "{ key:'strength_1m', label:'1분강도', className:'num metric-header', sort:'strength_1m', width:70, min:54 },",
        "{ key:'strength_5m', label:'5분강도', className:'num metric-header', sort:'strength_5m', width:70, min:54 },",
    )
    html = html.replace("    strength_1m: 58,", "    strength_5m: 58,")
    html = html.replace(
        "    const one=numeric(r.strength_1m);",
        "    const five=numeric(r.strength_5m);",
    )
    html = re.sub(
        r"\$\{metricCell\('strength_1m',one,fmtStrength\(one\),clsStrength\(one\)\+cellFlashClass\(code,'strength_1m',one\),'[^']*'\)\}",
        "${metricCell('strength_5m',five,fmtStrength(five),clsStrength(five)+cellFlashClass(code,'strength_5m',five),'키움 opt10046 5분강도 · 마지막 정상값 유지')}",
        html,
        count=1,
    )

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* STOCKBOARD_V2_STRENGTH5M_DISPLAY_20260710 */
  deriveClientFields = function(row){
    return row && typeof row === 'object' ? {...row} : {};
  };
'''
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def _strip_noisy_tooltips_patch(html: str) -> str:
    """Keep only the daily candle tooltip and remove row/cell metric tooltips."""

    html = html.replace(
        "function candleHtml(r){",
        "function candleTitle(r,o){const rate=Number(r.change_rate);let base=numeric(r.prev_close??r.prev_close_price??r.prev_price??r.yesterday_close??r.base_price??r.reference_price??r.prev_day_close);if((base===null||base<=0)&&Number.isFinite(Number(o.close))&&Number.isFinite(rate)&&rate!==-100){base=Number(o.close)/(1+rate/100);}const rateFor=value=>{const n=Number(value);if(!Number.isFinite(n)||base===null||base<=0)return '-';const pct=(n/base-1)*100;return `${pct>0?'+':''}${pct.toFixed(2)}%`;};const line=(label,value)=>`${label} ${fmtNum(value)} (${rateFor(value)})`;return `${line('시가',o.open)}\\n${line('고가',o.high)}\\n${line('저가',o.low)}\\n${line('종가',o.close)}`;} function candleHtml(r){",
        1,
    )
    html = html.replace(
        'title="O ${fmtNum(o.open)} H ${fmtNum(o.high)} L ${fmtNum(o.low)} C ${fmtNum(o.close)}"',
        'title="${escapeHtml(candleTitle(r,o))}"',
    )
    html = html.replace(' title="${escapeHtml(rowTitle(r))}"', "")
    html = html.replace(' title="score ${r.grade_score??\'-\'}"', "")
    html = html.replace(' title="?? ${fmtNum(r.prev_trade_value_eok,0)}?"', "")
    html = html.replace(' title="${escapeHtml(fullTitle)}"', "")
    return html


def _ui_safety_patch(html: str) -> str:
    marker = "STOCKBOARD_V2_SAFE_NAV_SCROLL_20260710"
    if marker in html:
        return html

    html = html.replace(
        "const now=performance.now(),shouldPool=opt.forcePool||now-lastPoolRenderAt>=250,",
        "const now=performance.now(),shouldPool=opt.forcePool||now-lastPoolRenderAt>=__sbv2PoolIntervalMs(),",
    )

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = r'''
  /* STOCKBOARD_V2_SAFE_NAV_SCROLL_20260710 */
  function __sbv2PoolIntervalMs(){
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    return minutes >= 9 * 60 && minutes < 9 * 60 + 10 ? 1000 : 250;
  }

  let __sbv2LastNavTable = null;
  function __sbv2IsBoardTable(table){ return table === selectedBoardEl || table === focusBoardEl || table === poolBoardEl; }
  function __sbv2IsPoolLikeTable(table){ return table === focusBoardEl || table === poolBoardEl; }
  function __sbv2TableHasCode(table, code){ return !!(table && code && table.querySelector(`tbody tr[data-code="${code}"]`)); }
  function __sbv2RememberNavTable(event){
    const row = event && event.target && event.target.closest ? event.target.closest('tr[data-code]') : null;
    const table = row ? row.closest('table') : null;
    if(__sbv2IsBoardTable(table)) __sbv2LastNavTable = table;
  }
  document.addEventListener('pointerdown', __sbv2RememberNavTable, true);
  document.addEventListener('mousedown', __sbv2RememberNavTable, true);

  function __sbv2RowsInTable(table){
    const body = table && table.tBodies ? table.tBodies[0] : null;
    return Array.from(body ? body.querySelectorAll('tr[data-code]') : [])
      .filter(row => /^\d{6}$/.test(String(row.dataset.code || '')));
  }
  function __sbv2NavTable(){
    const active = document.activeElement;
    const activeRow = active && active.closest ? active.closest('tr[data-code]') : null;
    const activeTable = activeRow ? activeRow.closest('table') : null;
    if(selectedCode && __sbv2IsPoolLikeTable(__sbv2LastNavTable) && __sbv2TableHasCode(__sbv2LastNavTable, selectedCode)) return __sbv2LastNavTable;
    if(__sbv2IsPoolLikeTable(activeTable)) return activeTable;
    if(__sbv2IsBoardTable(__sbv2LastNavTable)) return __sbv2LastNavTable;
    if(selectedCode){
      for(const table of [focusBoardEl, poolBoardEl, selectedBoardEl]){
        if(__sbv2IsBoardTable(table) && __sbv2TableHasCode(table, selectedCode)) return table;
      }
    }
    return focusBoardEl || poolBoardEl || selectedBoardEl;
  }
  function __sbv2RefocusVisibleRow(table, code){
    const row = table && table.querySelector ? table.querySelector(`tbody tr[data-code="${code}"]`) : null;
    if(row && typeof row.focus === 'function'){
      row.focus({preventScroll:true});
      if(typeof row.scrollIntoView === 'function') row.scrollIntoView({block:'nearest', inline:'nearest'});
      return true;
    }
    return false;
  }
  const __sbv2OriginalFocusSelectedRow = typeof focusSelectedRow === 'function' ? focusSelectedRow : null;
  if(__sbv2OriginalFocusSelectedRow){
    focusSelectedRow = function(preventScroll=true){
      if(selectedCode){
        for(const table of [__sbv2LastNavTable, focusBoardEl, poolBoardEl, selectedBoardEl]){
          if(__sbv2IsBoardTable(table) && __sbv2RefocusVisibleRow(table, selectedCode)) return;
        }
      }
      return __sbv2OriginalFocusSelectedRow(preventScroll);
    };
  }
  function __sbv2MoveByVisibleRows(delta){
    const table = __sbv2NavTable();
    const rows = __sbv2RowsInTable(table);
    if(!rows.length) return false;
    const active = document.activeElement;
    const activeRow = active && active.closest ? active.closest('tr[data-code]') : null;
    let index = rows.findIndex(row => row === activeRow || String(row.dataset.code || '') === selectedCode);
    if(index < 0) index = delta > 0 ? -1 : 0;
    const nextRow = rows[(index + delta + rows.length) % rows.length];
    const code = String(nextRow && nextRow.dataset.code || '');
    if(!/^\d{6}$/.test(code)) return false;
    __sbv2LastNavTable = table;
    __sbv2RefocusVisibleRow(table, code);
    selectCodeAndLink(code, false, {focus:false});
    [0, 80, 180, 360, 720, 1200].forEach(delay => {
      setTimeout(() => __sbv2RefocusVisibleRow(table, code), delay);
    });
    return true;
  }
  const __sbv2OriginalMoveSelection = moveSelection;
  moveSelection = function(delta){ if(!__sbv2MoveByVisibleRows(delta)) return __sbv2OriginalMoveSelection(delta); };
  function __sbv2HandleArrowKey(event){
    if(event.__sbv2Handled) return;
    if(event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    const target = event.target;
    if(target && target.closest && target.closest('input,textarea,select,button,[contenteditable="true"]')) return;
    if(!document.querySelector('table.board tbody tr[data-code]')) return;
    event.__sbv2Handled = true;
    event.preventDefault();
    event.stopImmediatePropagation();
    moveSelection(event.key === 'ArrowDown' ? 1 : -1);
  }
  document.addEventListener('keydown', __sbv2HandleArrowKey, true);
  window.addEventListener('keydown', __sbv2HandleArrowKey, true);

  if(rowPositionToggle){ rowPositionToggle.style.display = 'none'; }

  function __sbv2ScrollSpacer(){
    let spacer = document.getElementById('sbv2-horizontal-scroll-spacer');
    if(!spacer){
      spacer = document.createElement('div');
      spacer.id = 'sbv2-horizontal-scroll-spacer';
      spacer.setAttribute('aria-hidden', 'true');
      spacer.style.height = '1px';
      spacer.style.pointerEvents = 'none';
      spacer.style.visibility = 'hidden';
      document.body.appendChild(spacer);
    }
    return spacer;
  }
  function __sbv2ColumnSum(){
    try { return columns.reduce((sum, _c, i) => sum + Number(columnWidth(i) || 0), 0); }
    catch(_e) { return 0; }
  }
  function __sbv2BoardWidth(){
    const cssWidth = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--board-width')) || 0;
    const tableWidths = Array.from(document.querySelectorAll('table.board'))
      .map(table => Math.max(table.scrollWidth || 0, table.getBoundingClientRect().width || 0));
    return Math.ceil(Math.max(cssWidth, __sbv2ColumnSum(), ...tableWidths, 0));
  }
  function __sbv2ApplyHorizontalScrollFix(){
    const boardWidth = __sbv2BoardWidth();
    const width = Math.ceil(Math.max(boardWidth + 24, window.innerWidth));
    document.documentElement.style.overflowX = 'auto';
    document.body.style.overflowX = 'auto';
    document.documentElement.style.minWidth = `${width}px`;
    document.body.style.minWidth = `${width}px`;
    __sbv2ScrollSpacer().style.width = `${width}px`;
  }
  const __sbv2OriginalUpdateBoardWidth = typeof updateBoardWidth === 'function' ? updateBoardWidth : null;
  if(__sbv2OriginalUpdateBoardWidth){
    updateBoardWidth = function(...args){
      const result = __sbv2OriginalUpdateBoardWidth.apply(this, args);
      requestAnimationFrame(__sbv2ApplyHorizontalScrollFix);
      return result;
    };
  }
  const __sbv2OriginalRenderForScroll = render;
  render = function(...args){
    const result = __sbv2OriginalRenderForScroll.apply(this, args);
    requestAnimationFrame(__sbv2ApplyHorizontalScrollFix);
    return result;
  };
  window.addEventListener('resize', () => requestAnimationFrame(__sbv2ApplyHorizontalScrollFix));
  setTimeout(__sbv2ApplyHorizontalScrollFix, 0);
  setTimeout(__sbv2ApplyHorizontalScrollFix, 300);
  setTimeout(__sbv2ApplyHorizontalScrollFix, 1000);
'''
    if anchor not in html:
        return html
    return html.replace(anchor, f"{patch}\n{anchor}", 1)


def _patched_do_get(self) -> None:
    parsed = base.urlparse(self.path)
    if parsed.path in {"/", "/v2", "/stockboard_v2.html"}:
        html_path = Path(base.ROOT) / "docs" / "stockboard_v2.html"
        html = html_path.read_text(encoding="utf-8-sig")
        html = _five_min_strength_display_patch(html)
        html = _strip_noisy_tooltips_patch(html)
        html = _ui_safety_patch(html)
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
