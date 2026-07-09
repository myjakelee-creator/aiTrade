from __future__ import annotations

import importlib
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import Any

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


def _patched_do_get(self) -> None:
    parsed = base.urlparse(self.path)
    if parsed.path in {"/", "/v2", "/stockboard_v2.html"}:
        html_path = Path(base.ROOT) / "docs" / "stockboard_v2.html"
        body = _large_trade_title_patch(html_path.read_text(encoding="utf-8-sig")).encode("utf-8")
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
