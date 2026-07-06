"""Daily realtime large-trade accumulator for StockBoard.

The RealtimeStore already calculates rolling one-minute large-trade metrics.
This patch adds a separate day-level accumulator that survives a server restart
by saving snapshots under data/runtime/large_trade_accum.

Policy:
- Large trade threshold: 50,000,000 KRW per execution.
- Buy/sell direction follows the sign of Kiwoom realtime trade_qty.
- The public fields intentionally reuse large_trade_* / big_hand_* so the
  existing UI column displays the daily accumulated count without extra UI work.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
KRW_PER_EOK = 100_000_000
LARGE_TRADE_THRESHOLD_KRW = 50_000_000
LARGE_TRADE_THRESHOLD_EOK = LARGE_TRADE_THRESHOLD_KRW / KRW_PER_EOK
SCHEMA_VERSION = 1
SAVE_INTERVAL_SEC = 2.0


def _runtime_dir() -> Path:
    configured = os.getenv("STOCKBOARD_RUNTIME_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parent / "data" / "runtime"


def _today_kst() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _trading_date_from_timestamp(timestamp_text: Any) -> str:
    if timestamp_text:
        try:
            dt = datetime.fromisoformat(str(timestamp_text).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return dt.astimezone(KST).strftime("%Y%m%d")
        except (TypeError, ValueError):
            pass
    return _today_kst()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _code_text(stock_code: Any) -> str:
    text = str(stock_code or "").strip().upper()
    if text.startswith("A") and len(text) == 7:
        text = text[1:]
    text = text.replace("_AL", "").replace("_NX", "")
    return text if len(text) == 6 and text.isdigit() else ""


def _accum_path(trading_date: str) -> Path:
    return _runtime_dir() / "large_trade_accum" / f"large_trade_accum_{trading_date}.json"


def _new_entry(code: str, trading_date: str) -> dict[str, Any]:
    return {
        "stock_code": code,
        "trading_date": trading_date,
        "large_trade_source": "realtime_daily_accum",
        "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
        "large_trade_threshold_eok": LARGE_TRADE_THRESHOLD_EOK,
        "large_trade_buy_count": 0,
        "large_trade_sell_count": 0,
        "large_trade_net_count": 0,
        "large_trade_buy_sum_eok": 0.0,
        "large_trade_sell_sum_eok": 0.0,
        "large_trade_net_sum_eok": 0.0,
        "large_trade_unknown_count": 0,
        "large_trade_unknown_sum_eok": 0.0,
        "large_trade_updated_at": None,
        "large_trade_status": "ok",
    }


def _normalize_entry(raw: dict[str, Any], code: str, trading_date: str) -> dict[str, Any]:
    entry = _new_entry(code, trading_date)
    if not isinstance(raw, dict):
        return entry
    for key in tuple(entry):
        if key in raw and raw[key] not in (None, ""):
            entry[key] = deepcopy(raw[key])
    entry["stock_code"] = code
    entry["trading_date"] = trading_date
    _recompute_aliases(entry)
    return entry


def _recompute_aliases(entry: dict[str, Any]) -> dict[str, Any]:
    buy_count = int(_number(entry.get("large_trade_buy_count")) or 0)
    sell_count = int(_number(entry.get("large_trade_sell_count")) or 0)
    unknown_count = int(_number(entry.get("large_trade_unknown_count")) or 0)
    buy_sum = float(_number(entry.get("large_trade_buy_sum_eok")) or 0.0)
    sell_sum = float(_number(entry.get("large_trade_sell_sum_eok")) or 0.0)
    unknown_sum = float(_number(entry.get("large_trade_unknown_sum_eok")) or 0.0)
    net_count = buy_count - sell_count
    net_sum = buy_sum - sell_sum
    entry.update(
        {
            "large_trade_buy_count": buy_count,
            "large_trade_sell_count": sell_count,
            "large_trade_net_count": net_count,
            "large_trade_buy_sum_eok": round(buy_sum, 4),
            "large_trade_sell_sum_eok": round(sell_sum, 4),
            "large_trade_net_sum_eok": round(net_sum, 4),
            "large_trade_unknown_count": unknown_count,
            "large_trade_unknown_sum_eok": round(unknown_sum, 4),
            "large_trade_source": "realtime_daily_accum",
            "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
            "large_trade_threshold_eok": LARGE_TRADE_THRESHOLD_EOK,
            "large_trade_status": "ok",
            # Existing UI aliases. The historical suffix says 1eok but the
            # current project threshold is 50m KRW.
            "big_hand_buy_count_1eok": buy_count,
            "big_hand_sell_count_1eok": sell_count,
            "big_hand_net_buy_count_1eok": net_count,
            "big_hand_buy_sum_eok": round(buy_sum, 4),
            "big_hand_sell_sum_eok": round(sell_sum, 4),
            "big_hand_net_sum_eok": round(net_sum, 4),
        }
    )
    return entry


def _load_payload(path: Path, trading_date: str) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {"schema_version": SCHEMA_VERSION, "trading_date": trading_date, "entries": {}}
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {"schema_version": SCHEMA_VERSION, "trading_date": trading_date, "entries": {}}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            payload["entries"] = {}
        return payload
    except (OSError, json.JSONDecodeError) as error:
        print(f"warning: large trade accumulator load failed: {error}", flush=True)
        return {"schema_version": SCHEMA_VERSION, "trading_date": trading_date, "entries": {}}


def _save_payload(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    except OSError as error:
        print(f"warning: large trade accumulator save failed: {error}", flush=True)


def _ensure_state(store: Any, trading_date: str) -> dict[str, Any]:
    current_date = getattr(store, "_large_trade_accum_date", None)
    current_payload = getattr(store, "_large_trade_accum_payload", None)
    if current_date == trading_date and isinstance(current_payload, dict):
        return current_payload

    path = _accum_path(trading_date)
    payload = _load_payload(path, trading_date)
    normalized_entries: dict[str, Any] = {}
    for raw_code, raw_entry in (payload.get("entries") or {}).items():
        code = _code_text(raw_entry.get("stock_code") if isinstance(raw_entry, dict) else raw_code)
        if not code:
            code = _code_text(raw_code)
        if not code:
            continue
        normalized_entries[code] = _normalize_entry(raw_entry, code, trading_date)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "trading_date": trading_date,
        "updated_at": payload.get("updated_at") or _now_iso(),
        "entries": normalized_entries,
    }
    store._large_trade_accum_date = trading_date
    store._large_trade_accum_path = path
    store._large_trade_accum_payload = payload
    store._large_trade_accum_last_save = time.monotonic()
    return payload


def _save_if_due(store: Any, *, force: bool = False) -> None:
    payload = getattr(store, "_large_trade_accum_payload", None)
    path = getattr(store, "_large_trade_accum_path", None)
    if not isinstance(payload, dict) or path is None:
        return
    now = time.monotonic()
    last_save = float(getattr(store, "_large_trade_accum_last_save", 0.0) or 0.0)
    if not force and now - last_save < SAVE_INTERVAL_SEC:
        return
    payload["updated_at"] = _now_iso()
    _save_payload(Path(path), payload)
    store._large_trade_accum_last_save = now


def _quote_metrics(store: Any, code: str) -> dict[str, Any] | None:
    trading_date = _today_kst()
    payload = _ensure_state(store, trading_date)
    entry = payload.get("entries", {}).get(code)
    return deepcopy(entry) if isinstance(entry, dict) else None


def _apply_restored_metrics_to_quote(store: Any, quote: dict[str, Any]) -> None:
    code = _code_text(quote.get("stock_code"))
    if not code:
        return
    metrics = _quote_metrics(store, code)
    if metrics:
        quote.update(metrics)


def _apply_trade_to_accumulator(store: Any, code: str, kwargs: dict[str, Any], returned_quote: dict[str, Any]) -> dict[str, Any] | None:
    if kwargs.get("stale_trade_suspect"):
        return None
    qty = _number(kwargs.get("trade_qty"))
    if not qty:
        return None
    price = _number(kwargs.get("trade_price"))
    if price is None:
        price = _number(kwargs.get("price"))
    if price is None or price <= 0:
        return None
    amount_krw = abs(qty) * abs(price)
    if amount_krw < LARGE_TRADE_THRESHOLD_KRW:
        return None

    timestamp_text = (
        returned_quote.get("trade_received_at")
        or kwargs.get("received_at")
        or _now_iso()
    )
    trading_date = _trading_date_from_timestamp(timestamp_text)
    payload = _ensure_state(store, trading_date)
    entries = payload.setdefault("entries", {})
    entry = entries.setdefault(code, _new_entry(code, trading_date))
    amount_eok = amount_krw / KRW_PER_EOK
    if qty > 0:
        entry["large_trade_buy_count"] = int(entry.get("large_trade_buy_count") or 0) + 1
        entry["large_trade_buy_sum_eok"] = float(entry.get("large_trade_buy_sum_eok") or 0.0) + amount_eok
    elif qty < 0:
        entry["large_trade_sell_count"] = int(entry.get("large_trade_sell_count") or 0) + 1
        entry["large_trade_sell_sum_eok"] = float(entry.get("large_trade_sell_sum_eok") or 0.0) + amount_eok
    else:
        entry["large_trade_unknown_count"] = int(entry.get("large_trade_unknown_count") or 0) + 1
        entry["large_trade_unknown_sum_eok"] = float(entry.get("large_trade_unknown_sum_eok") or 0.0) + amount_eok
    entry["large_trade_updated_at"] = timestamp_text
    entry["updated_at"] = timestamp_text
    _recompute_aliases(entry)
    _save_if_due(store)
    return deepcopy(entry)


def install_large_trade_accumulator_patch() -> None:
    import stockboard_store

    store_class = stockboard_store.RealtimeStore
    if getattr(store_class, "_large_trade_accumulator_patch_installed", False):
        return

    original_ensure_quote = store_class._ensure_quote
    original_update_trade = store_class.update_trade

    def ensure_quote_with_large_trade_accum(self, stock_code):
        quote = original_ensure_quote(self, stock_code)
        try:
            _apply_restored_metrics_to_quote(self, quote)
        except Exception as error:
            quote["large_trade_status"] = f"accum_restore_error: {error}"
        return quote

    def update_trade_with_large_trade_accum(self, stock_code, *args, **kwargs):
        returned = original_update_trade(self, stock_code, *args, **kwargs)
        try:
            code = self._normalized_code(stock_code)
            metrics = _apply_trade_to_accumulator(self, code, kwargs, returned)
            if metrics:
                with self._lock:
                    quote = original_ensure_quote(self, code)
                    quote.update(metrics)
                    returned.update(metrics)
        except Exception as error:
            returned["large_trade_status"] = f"accum_error: {error}"
        return returned

    store_class._ensure_quote = ensure_quote_with_large_trade_accum
    store_class.update_trade = update_trade_with_large_trade_accum
    store_class._large_trade_accumulator_patch_installed = True
