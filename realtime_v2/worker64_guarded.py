from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2 import worker64 as base
from realtime_v2.common import (
    LARGE_TRADE_THRESHOLD_KRW,
    RUNTIME_DIR,
    event_age_sec,
    normalize_code,
    normalize_trade_time,
    normalized_price,
    normalized_rate,
    normalized_trade_value_eok,
    now_text,
    to_int,
    to_number,
    trading_date_text,
)
from realtime_v2.market_session import market_session_now
from stockboard_ranking_engine import enrich_net_buy_strength_v02_fields

STALE_LAG_WARN_SEC = 10.0
ONE_MIN_STRENGTH_CAP = 999.99
PREVIOUS_VALUE_KEYS = (
    "prev_trade_value_eok",
    "prev_trade_value_source",
    "prev_trade_value_status",
    "prev_trade_value_date",
    "prev_trade_value_lookup_source",
)
PERSISTED_LIVE_KEYS = (
    "execution_strength",
    "execution_strength_updated_at",
    "ask_volume",
    "bid_volume",
    "ask_pct",
    "bid_pct",
    "bid_ask_ratio",
    "best_ask_price",
    "best_bid_price",
    "orderbook_received_at",
    "day_open",
    "day_high",
    "day_low",
    "day_close",
    "ohlc",
    "strength_1m",
    "one_min_buy_qty",
    "one_min_sell_qty",
    "one_min_strength_updated_at",
    "regular_close_strength_1m",
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "strength_source",
    "strength_snapshot_at",
    "strength_status",
    "strength_display_basis",
    "regular_close_bid_ask_ratio",
    "regular_close_bid_pct",
    "regular_close_ask_pct",
    "regular_close_bid_volume",
    "regular_close_ask_volume",
    "regular_close_orderbook_at",
    "orderbook_display_basis",
    "last_valid_bid_ask_ratio",
    "last_valid_bid_pct",
    "last_valid_ask_pct",
    "last_valid_bid_volume",
    "last_valid_ask_volume",
    "last_valid_orderbook_at",
)
base.DAILY_PERSIST_KEYS = tuple(dict.fromkeys((*base.DAILY_PERSIST_KEYS, *PERSISTED_LIVE_KEYS)))


def _time_seconds(value: Any) -> int | None:
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    if len(digits) < 6:
        return None
    digits = digits[:6]
    try:
        hour, minute, second = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    except ValueError:
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def _now_seconds() -> int:
    now = datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second


def _lag_seconds(trade_time_seconds: int | None) -> float | None:
    if trade_time_seconds is None:
        return None
    lag = _now_seconds() - int(trade_time_seconds)
    if lag < -12 * 3600:
        lag += 24 * 3600
    return round(max(0.0, float(lag)), 3)


def _json_file(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                return payload
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _load_json_first(paths: list[Path]) -> dict[str, Any] | None:
    for path in paths:
        payload = _json_file(path)
        if payload is not None:
            return payload
    return None


def _session_dict() -> dict[str, Any]:
    try:
        return market_session_now().to_dict()
    except Exception as error:
        return {
            "phase": "unknown",
            "phase_label": "장상태 알 수 없음",
            "trading_date": "",
            "calendar_date": "",
            "accept_realtime": True,
            "prefer_seed_when_no_realtime": True,
            "freeze_realtime_missing": True,
            "reason": str(error),
            "windows": {},
            "special_day": {},
            "is_trading_day": False,
        }


def _update_market_session_status(state) -> dict[str, Any]:
    session = _session_dict()
    state.status["market_phase"] = session.get("phase")
    state.status["market_phase_label"] = session.get("phase_label")
    state.status["market_trading_date"] = session.get("trading_date")
    state.status["market_accept_realtime"] = session.get("accept_realtime")
    state.status["market_freeze_realtime_missing"] = session.get("freeze_realtime_missing")
    previous_date = state.status.get("active_trading_date")
    current_date = session.get("trading_date")
    if current_date and previous_date and previous_date != current_date:
        state.status["date_rollover_detected"] = True
        state.status["date_rollover_from"] = previous_date
        state.status["date_rollover_to"] = current_date
        state.status["needs_universe_rebuild"] = True
    elif current_date and not previous_date:
        state.status["active_trading_date"] = current_date
    return session


def _previous_trade_value_cache_paths() -> list[Path]:
    date_text = trading_date_text()
    return [ROOT / "data" / "runtime" / f"previous_trade_value_{date_text}.json", RUNTIME_DIR / f"previous_trade_value_{date_text}.json"]


def _load_previous_trade_value_cache() -> dict[str, dict[str, Any]]:
    for path in _previous_trade_value_cache_paths():
        payload = _json_file(path)
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            continue
        result: dict[str, dict[str, Any]] = {}
        for raw_code, raw_entry in entries.items():
            code = normalize_code(raw_code)
            if not code or not isinstance(raw_entry, dict):
                continue
            value = to_number(raw_entry.get("prev_trade_value_eok"))
            if value is not None and value > 0:
                result[code] = dict(raw_entry)
        return result
    return {}


def _previous_entry_for_code(state, code: str) -> dict[str, Any]:
    code = normalize_code(code)
    cache = getattr(state, "prev_trade_value_cache_by_code", {}) or {}
    entry = cache.get(code)
    return entry if isinstance(entry, dict) else {}


def _previous_value_for_code(state, code: str) -> float | None:
    code = normalize_code(code)
    value = to_number(getattr(state, "prev_trade_value_by_code", {}).get(code))
    if value is not None and value > 0:
        return float(value)
    entry_value = to_number(_previous_entry_for_code(state, code).get("prev_trade_value_eok"))
    if entry_value is not None and entry_value > 0:
        state.prev_trade_value_by_code[code] = float(entry_value)
        return float(entry_value)
    return None


def _copy_previous_fields(state, code: str, target: dict[str, Any], seed: dict[str, Any] | None = None) -> None:
    code = normalize_code(code)
    seed = seed or {}
    previous_value = to_number(target.get("prev_trade_value_eok"))
    if previous_value is None or previous_value <= 0:
        previous_value = _previous_value_for_code(state, code)
    if previous_value is not None and previous_value > 0:
        target["prev_trade_value_eok"] = round(float(previous_value), 4)
        target.pop("prev_trade_value_missing", None)
        if code:
            state.prev_trade_value_by_code[code] = float(previous_value)
    else:
        target["prev_trade_value_missing"] = True
    entry = _previous_entry_for_code(state, code)
    for key in PREVIOUS_VALUE_KEYS[1:]:
        if target.get(key) in (None, ""):
            value = seed.get(key) if isinstance(seed, dict) else None
            if value in (None, ""):
                value = entry.get(key)
            if value not in (None, ""):
                target[key] = value




def _restore_persisted_live_metrics(quote: dict[str, Any], persisted: dict[str, Any]) -> None:
    if not isinstance(quote, dict) or not isinstance(persisted, dict):
        return
    for key in PERSISTED_LIVE_KEYS:
        if key in persisted:
            quote[key] = deepcopy(persisted.get(key))


def _persist_live_metrics(state, code: str, quote: dict[str, Any], *keys: str) -> None:
    if not code or not isinstance(quote, dict):
        return
    entry = state.daily_values_by_code.setdefault(code, {})
    changed = False
    for key in keys:
        value = quote.get(key)
        if value not in (None, "") and entry.get(key) != value:
            entry[key] = deepcopy(value)
            changed = True
    if changed and hasattr(state, "_mark_daily_dirty"):
        state._mark_daily_dirty()


DISPLAY_FALLBACK_KEYS = tuple(dict.fromkeys((
    "bid_ask_ratio",
    "bid_pct",
    "ask_pct",
    "bid_volume",
    "ask_volume",
    "regular_close_bid_ask_ratio",
    "regular_close_bid_pct",
    "regular_close_ask_pct",
    "regular_close_bid_volume",
    "regular_close_ask_volume",
    "last_valid_bid_ask_ratio",
    "last_valid_bid_pct",
    "last_valid_ask_pct",
    "last_valid_bid_volume",
    "last_valid_ask_volume",
    "strength_1m",
    "one_min_strength",
    "regular_close_strength_1m",
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "execution_strength",
    "one_min_strength_status",
    "strength_display_basis",
    "strength_source",
    "strength_snapshot_at",
    "strength_status",
    "last_valid_strength_1m",
    "last_valid_strength_5m",
    "last_valid_execution_strength",
    "last_valid_strength_at",
    "large_trade_buy_count",
    "large_trade_sell_count",
    "large_trade_net_count",
    "large_trade_buy_sum_eok",
    "large_trade_sell_sum_eok",
    "large_trade_net_sum_eok",
)))


def _previous_daily_state_paths() -> list[Path]:
    current = str(trading_date_text())
    paths = []
    try:
        for path in RUNTIME_DIR.glob("daily_state_*.json"):
            if current and current in path.stem:
                continue
            paths.append(path)
    except OSError:
        return []
    return sorted(paths, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)


def _load_previous_daily_display_values_if_needed(state) -> dict[str, dict[str, Any]]:
    loaded = getattr(state, "previous_daily_display_values_by_code", None)
    if isinstance(loaded, dict):
        return loaded

    result: dict[str, dict[str, Any]] = {}
    source_path = None

    for path in _previous_daily_state_paths():
        payload = _json_file(path) or {}
        codes = payload.get("codes") if isinstance(payload, dict) else None
        if not isinstance(codes, dict):
            continue

        for raw_code, values in codes.items():
            code = normalize_code(raw_code)
            if code and isinstance(values, dict):
                result[code] = dict(values)

        if result:
            source_path = path
            break

    state.previous_daily_display_values_by_code = result
    state.status["previous_daily_display_count"] = len(result)
    if source_path is not None:
        state.status["previous_daily_display_source"] = str(source_path)
    return result


def _should_use_previous_daily_display(session: dict[str, Any] | None) -> bool:
    session = session or {}
    phase = str(session.get("phase") or "").lower()
    label = str(session.get("phase_label") or "")

    # ????? ?? display fallback? ?? ???.
    if "regular" in phase or "???" in label:
        return False

    return True


def _fallback_number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    return float(number)


def _fallback_value_is_usable(key: str, value: Any) -> bool:
    if value in (None, ""):
        return False

    number = _fallback_number(value)

    if key in {
        "bid_ask_ratio",
        "regular_close_bid_ask_ratio",
        "last_valid_bid_ask_ratio",
        "strength_1m",
        "one_min_strength",
        "regular_close_strength_1m",
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "execution_strength",
        "last_valid_strength_1m",
        "last_valid_strength_5m",
        "last_valid_execution_strength",
    }:
        return number is not None and number > 0

    if key in {
        "bid_pct",
        "ask_pct",
        "bid_volume",
        "ask_volume",
        "regular_close_bid_pct",
        "regular_close_ask_pct",
        "regular_close_bid_volume",
        "regular_close_ask_volume",
        "last_valid_bid_pct",
        "last_valid_ask_pct",
        "last_valid_bid_volume",
        "last_valid_ask_volume",
        "large_trade_buy_count",
        "large_trade_sell_count",
        "large_trade_buy_sum_eok",
        "large_trade_sell_sum_eok",
    }:
        return number is not None and number > 0

    if key in {
        "large_trade_net_count",
        "large_trade_net_sum_eok",
    }:
        return number is not None and number != 0

    return True


def _current_value_is_missing_for_display(key: str, value: Any) -> bool:
    if value in (None, ""):
        return True

    number = _fallback_number(value)

    if key in {
        "bid_ask_ratio",
        "regular_close_bid_ask_ratio",
        "last_valid_bid_ask_ratio",
        "strength_1m",
        "one_min_strength",
        "regular_close_strength_1m",
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "execution_strength",
        "last_valid_strength_1m",
        "last_valid_strength_5m",
        "last_valid_execution_strength",
        "bid_pct",
        "ask_pct",
        "bid_volume",
        "ask_volume",
        "regular_close_bid_pct",
        "regular_close_ask_pct",
        "regular_close_bid_volume",
        "regular_close_ask_volume",
        "last_valid_bid_pct",
        "last_valid_ask_pct",
        "last_valid_bid_volume",
        "last_valid_ask_volume",
        "large_trade_buy_count",
        "large_trade_sell_count",
        "large_trade_buy_sum_eok",
        "large_trade_sell_sum_eok",
    }:
        return number is None or number <= 0

    if key in {
        "large_trade_net_count",
        "large_trade_net_sum_eok",
    }:
        return number is None or number == 0

    return False


def _apply_previous_daily_display_fallback(state, row: dict[str, Any], session: dict[str, Any] | None) -> None:
    if not _should_use_previous_daily_display(session):
        return

    code = normalize_code(row.get("stock_code"))
    if not code:
        return

    previous_values = _load_previous_daily_display_values_if_needed(state).get(code)
    if not isinstance(previous_values, dict):
        return

    applied = 0
    for key in DISPLAY_FALLBACK_KEYS:
        if key not in previous_values:
            continue
        previous_value = previous_values.get(key)
        if not _fallback_value_is_usable(key, previous_value):
            continue
        if _current_value_is_missing_for_display(key, row.get(key)):
            row[key] = deepcopy(previous_value)
            applied += 1

    if applied:
        row["previous_daily_display_fallback"] = True

def _ohlc_snapshot_path() -> Path:
    return RUNTIME_DIR / "ohlc_snapshot.json"


def _load_ohlc_snapshot_if_needed(state, force: bool = False) -> None:
    path = _ohlc_snapshot_path()
    now_mono = time.monotonic()
    last_check = float(getattr(state, "ohlc_snapshot_last_check", 0.0) or 0.0)
    if not force and now_mono - last_check < 5.0:
        return
    state.ohlc_snapshot_last_check = now_mono
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    if not force and mtime == getattr(state, "ohlc_snapshot_mtime", None):
        return
    payload = _json_file(path) or {}
    raw_values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(raw_values, dict):
        return
    values: dict[str, dict[str, Any]] = {}
    for raw_code, raw_ohlc in raw_values.items():
        code = normalize_code(raw_code)
        if not code or not isinstance(raw_ohlc, dict):
            continue
        open_price = to_number(raw_ohlc.get("open"))
        high_price = to_number(raw_ohlc.get("high"))
        low_price = to_number(raw_ohlc.get("low"))
        close_price = to_number(raw_ohlc.get("close"))
        if any(value is None or value <= 0 for value in (open_price, high_price, low_price, close_price)):
            continue
        values[code] = {
            "open": round(float(open_price), 4),
            "high": round(float(high_price), 4),
            "low": round(float(low_price), 4),
            "close": round(float(close_price), 4),
            "source": raw_ohlc.get("source") or payload.get("source") or "ohlc_snapshot",
            "date": raw_ohlc.get("date") or payload.get("trading_date"),
        }
    state.ohlc_by_code = values
    state.ohlc_snapshot_mtime = mtime
    state.status["ohlc_snapshot_count"] = len(values)
    state.status["ohlc_snapshot_loaded_at"] = now_text()
    state.status["ohlc_snapshot_source"] = str(path)


def _apply_ohlc_snapshot_to_quote(state, code: str, quote: dict[str, Any]) -> None:
    code = normalize_code(code)
    snapshot = getattr(state, "ohlc_by_code", {}).get(code)
    if not isinstance(snapshot, dict):
        return

    current = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
    current_source = str(current.get("source") or "")
    snapshot_source = str(snapshot.get("source") or "")

    def same_ohlc(left: dict[str, Any], right: dict[str, Any]) -> bool:
        for key in ("open", "high", "low", "close"):
            left_value = to_number(left.get(key))
            right_value = to_number(right.get(key))
            if left_value is None or right_value is None:
                return False
            if abs(float(left_value) - float(right_value)) > 0.0001:
                return False
        return True

    # AL ??? snapshot? ?? regular/old ka10086 ?? ??? ? ??? ??.
    # ??? ?? AL ?? ?? ???? regular fallback ??? downgrade?? ???.
    if current_source.startswith("ka10086_AL") and not snapshot_source.startswith("ka10086_AL"):
        return

    # ?? ??? ???? rewrite? ???.
    if current_source == snapshot_source and same_ohlc(current, snapshot):
        return

    quote["ohlc"] = deepcopy(snapshot)
    quote["day_open"] = snapshot.get("open")
    quote["day_high"] = snapshot.get("high")
    quote["day_low"] = snapshot.get("low")
    quote["day_close"] = snapshot.get("close")
    quote["ohlc_snapshot_applied_at"] = now_text()
    if current_source and current_source != snapshot_source:
        quote["ohlc_snapshot_replaced_source"] = current_source


def _update_intraday_ohlc(quote: dict[str, Any], price: float | int | None) -> None:
    value = to_number(price)
    if value is None or value <= 0:
        return
    current = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
    open_price = to_number(current.get("open") or quote.get("day_open")) or float(value)
    high_price = max(to_number(current.get("high") or quote.get("day_high")) or float(value), float(value))
    low_price = min(to_number(current.get("low") or quote.get("day_low")) or float(value), float(value))
    quote["ohlc"] = {
        "open": round(open_price, 4),
        "high": round(high_price, 4),
        "low": round(low_price, 4),
        "close": round(float(value), 4),
        "source": current.get("source") or "realtime_intraday",
        "date": current.get("date"),
    }
    quote["day_open"] = quote["ohlc"]["open"]
    quote["day_high"] = quote["ohlc"]["high"]
    quote["day_low"] = quote["ohlc"]["low"]
    quote["day_close"] = quote["ohlc"]["close"]


def _update_one_min_strength_flow(quote: dict[str, Any], *, buy_qty: int = 0, sell_qty: int = 0) -> None:
    buy_qty = max(0, int(buy_qty or 0))
    sell_qty = max(0, int(sell_qty or 0))
    if buy_qty <= 0 and sell_qty <= 0:
        return
    now_sec = int(time.monotonic())
    raw_buckets = quote.get("_one_min_qty_buckets") if isinstance(quote.get("_one_min_qty_buckets"), list) else []
    buckets: list[list[int]] = []
    for item in raw_buckets:
        try:
            sec, buy, sell = int(item[0]), int(item[1]), int(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if now_sec - sec <= 60:
            buckets.append([sec, buy, sell])
    if buckets and buckets[-1][0] == now_sec:
        bucket = buckets[-1]
    else:
        bucket = [now_sec, 0, 0]
        buckets.append(bucket)
    bucket[1] += buy_qty
    bucket[2] += sell_qty
    buckets = buckets[-61:]
    total_buy = sum(item[1] for item in buckets)
    total_sell = sum(item[2] for item in buckets)
    if total_sell > 0:
        strength = round(min(ONE_MIN_STRENGTH_CAP, total_buy / total_sell * 100.0), 4)
    elif total_buy > 0:
        strength = ONE_MIN_STRENGTH_CAP
    else:
        strength = None
    quote["_one_min_qty_buckets"] = buckets
    quote["one_min_buy_qty"] = total_buy
    quote["one_min_sell_qty"] = total_sell
    quote["strength_1m"] = strength
    quote["one_min_strength"] = strength
    quote["one_min_strength_updated_at"] = now_text()
    quote["one_min_strength_formula"] = "recent_60s_buy_qty/recent_60s_sell_qty*100"



def _strength_snapshot_paths() -> list[Path]:
    return [
        ROOT / "data" / "runtime" / "stockboard_close_metrics_snapshots.json",
        ROOT / "data" / "runtime" / "stockboard_v2" / "strength_snapshot.json",
    ]


def _load_strength_snapshot_if_needed(state, force: bool = False) -> None:
    now_mono = time.monotonic()
    last_check = float(getattr(state, "strength_snapshot_last_check", 0.0) or 0.0)
    if not force and now_mono - last_check < 5.0:
        return
    state.strength_snapshot_last_check = now_mono

    latest_path = None
    latest_mtime = None
    for path in _strength_snapshot_paths():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if latest_mtime is None or mtime > latest_mtime:
            latest_path = path
            latest_mtime = mtime

    if latest_path is None:
        return
    if not force and latest_mtime == getattr(state, "strength_snapshot_mtime", None):
        return

    payload = _json_file(latest_path) or {}
    raw_values = payload.get("snapshots") if isinstance(payload.get("snapshots"), dict) else payload.get("values")
    if not isinstance(raw_values, dict):
        return

    today = trading_date_text()
    values: dict[str, dict[str, Any]] = {}

    for raw_code, raw_snapshot in raw_values.items():
        if not isinstance(raw_snapshot, dict):
            continue
        code = normalize_code(raw_snapshot.get("stock_code") or raw_code)
        if not code:
            continue

        trading_date = str(raw_snapshot.get("trading_date") or today)
        digits = "".join(ch for ch in trading_date if ch.isdigit())
        if len(digits) >= 8 and digits[:8] != today:
            continue

        strength_5m = to_number(raw_snapshot.get("strength_5m"))
        if strength_5m is None:
            continue

        values[code] = {
            "strength_5m": round(float(strength_5m), 4),
            "strength_20m": to_number(raw_snapshot.get("strength_20m")),
            "strength_60m": to_number(raw_snapshot.get("strength_60m")),
            "strength_source": raw_snapshot.get("strength_source") or payload.get("source") or "opt10046_cached",
            "strength_snapshot_at": raw_snapshot.get("strength_snapshot_at") or raw_snapshot.get("updated_at") or payload.get("ts"),
            "strength_status": raw_snapshot.get("strength_status") or "cached",
        }

    state.strength_snapshot_by_code = values
    state.strength_snapshot_mtime = latest_mtime
    state.status["strength_snapshot_count"] = len(values)
    state.status["strength_snapshot_loaded_at"] = now_text()
    state.status["strength_snapshot_source"] = str(latest_path)


def _apply_strength_snapshot_to_quote(state, code: str, quote: dict[str, Any]) -> None:
    code = normalize_code(code)
    snapshot = getattr(state, "strength_snapshot_by_code", {}).get(code)
    if not isinstance(snapshot, dict):
        return
    if to_number(snapshot.get("strength_5m")) is None:
        return
    for key in ("strength_5m", "strength_20m", "strength_60m", "strength_source", "strength_snapshot_at", "strength_status"):
        if snapshot.get(key) not in (None, ""):
            quote[key] = snapshot.get(key)


def _is_aftermarket_session(session: dict[str, Any] | None) -> bool:
    session = session or {}
    phase = str(session.get("phase") or "").lower()
    label = str(session.get("phase_label") or "")

    # 15:30~15:40 after_wait, 15:40~20:00 aftermarket, 20:00 ?? closed ??
    # ??? ??? ?? ???? 5??? ?? ??? ????.
    if phase in {"after_wait", "aftermarket", "closed"}:
        return True
    return "after" in phase or "???" in label or "???" in label


def _is_regular_session(session: dict[str, Any] | None) -> bool:
    session = session or {}
    phase = str(session.get("phase") or "").lower()
    label = str(session.get("phase_label") or "")
    return "regular" in phase or "???" in label



def _valid_ratio_value(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    if float(number) <= 0:
        return None
    return float(number)


def _orderbook_snapshot_from_fields(
    quote: dict[str, Any],
    *,
    ratio_key: str,
    bid_pct_key: str,
    ask_pct_key: str,
    bid_volume_key: str,
    ask_volume_key: str,
    at_key: str,
    basis: str,
) -> dict[str, Any] | None:
    ratio = _valid_ratio_value(quote.get(ratio_key))
    bid_volume = to_number(quote.get(bid_volume_key))
    ask_volume = to_number(quote.get(ask_volume_key))
    bid_pct = to_number(quote.get(bid_pct_key))
    ask_pct = to_number(quote.get(ask_pct_key))

    if ratio is None and bid_volume is not None and ask_volume is not None:
        bid_volume = float(bid_volume)
        ask_volume = float(ask_volume)
        if bid_volume > 0 and ask_volume > 0:
            ratio = bid_volume / ask_volume
        elif bid_volume > 0 and ask_volume == 0:
            ratio = 20.0
        elif bid_volume == 0 and ask_volume > 0:
            ratio = 0.05

    if ratio is None and bid_pct is not None and ask_pct is not None:
        bid_pct = float(bid_pct)
        ask_pct = float(ask_pct)
        if bid_pct > 0 and ask_pct > 0:
            ratio = bid_pct / ask_pct
        elif bid_pct > 0 and ask_pct == 0:
            ratio = 20.0
        elif bid_pct == 0 and ask_pct > 0:
            ratio = 0.05

    if ratio is None:
        return None

    ratio = round(max(0.01, min(20.0, float(ratio))), 4)
    return {
        "bid_ask_ratio": ratio,
        "bid_pct": quote.get(bid_pct_key),
        "ask_pct": quote.get(ask_pct_key),
        "bid_volume": quote.get(bid_volume_key),
        "ask_volume": quote.get(ask_volume_key),
        "at": quote.get(at_key),
        "basis": basis,
    }


def _current_orderbook_snapshot(quote: dict[str, Any], basis: str = "?? ??? ??") -> dict[str, Any] | None:
    return _orderbook_snapshot_from_fields(
        quote,
        ratio_key="bid_ask_ratio",
        bid_pct_key="bid_pct",
        ask_pct_key="ask_pct",
        bid_volume_key="bid_volume",
        ask_volume_key="ask_volume",
        at_key="orderbook_received_at",
        basis=basis,
    )


def _regular_close_orderbook_snapshot(quote: dict[str, Any]) -> dict[str, Any] | None:
    return _orderbook_snapshot_from_fields(
        quote,
        ratio_key="regular_close_bid_ask_ratio",
        bid_pct_key="regular_close_bid_pct",
        ask_pct_key="regular_close_ask_pct",
        bid_volume_key="regular_close_bid_volume",
        ask_volume_key="regular_close_ask_volume",
        at_key="regular_close_orderbook_at",
        basis="??? ?? ??? ??",
    )


def _last_valid_orderbook_snapshot(quote: dict[str, Any]) -> dict[str, Any] | None:
    return _orderbook_snapshot_from_fields(
        quote,
        ratio_key="last_valid_bid_ask_ratio",
        bid_pct_key="last_valid_bid_pct",
        ask_pct_key="last_valid_ask_pct",
        bid_volume_key="last_valid_bid_volume",
        ask_volume_key="last_valid_ask_volume",
        at_key="last_valid_orderbook_at",
        basis="??? ?? ??? ??",
    )


def _apply_orderbook_snapshot(quote: dict[str, Any], snapshot: dict[str, Any]) -> None:
    quote["bid_ask_ratio"] = snapshot.get("bid_ask_ratio")
    if snapshot.get("bid_pct") not in (None, ""):
        quote["bid_pct"] = snapshot.get("bid_pct")
    if snapshot.get("ask_pct") not in (None, ""):
        quote["ask_pct"] = snapshot.get("ask_pct")
    if snapshot.get("bid_volume") not in (None, ""):
        quote["bid_volume"] = snapshot.get("bid_volume")
    if snapshot.get("ask_volume") not in (None, ""):
        quote["ask_volume"] = snapshot.get("ask_volume")
    quote["orderbook_display_basis"] = snapshot.get("basis")


def _remember_last_valid_orderbook_display(quote: dict[str, Any]) -> None:
    snapshot = _current_orderbook_snapshot(quote, "??? ?? ??? ??")
    if snapshot is None:
        return
    quote["last_valid_bid_ask_ratio"] = snapshot.get("bid_ask_ratio")
    quote["last_valid_bid_pct"] = snapshot.get("bid_pct")
    quote["last_valid_ask_pct"] = snapshot.get("ask_pct")
    quote["last_valid_bid_volume"] = snapshot.get("bid_volume")
    quote["last_valid_ask_volume"] = snapshot.get("ask_volume")
    quote["last_valid_orderbook_at"] = snapshot.get("at") or now_text()


def _remember_regular_orderbook_display(quote: dict[str, Any], session: dict[str, Any] | None) -> None:
    if not _is_regular_session(session):
        return
    snapshot = _current_orderbook_snapshot(quote, "??? ??? ??")
    if snapshot is None:
        return
    quote["regular_close_bid_ask_ratio"] = snapshot.get("bid_ask_ratio")
    quote["regular_close_bid_pct"] = snapshot.get("bid_pct")
    quote["regular_close_ask_pct"] = snapshot.get("ask_pct")
    quote["regular_close_bid_volume"] = snapshot.get("bid_volume")
    quote["regular_close_ask_volume"] = snapshot.get("ask_volume")
    quote["regular_close_orderbook_at"] = snapshot.get("at") or now_text()
    quote["orderbook_display_basis"] = "??? ??? ??"


def _apply_aftermarket_orderbook_display_policy(quote: dict[str, Any], session: dict[str, Any] | None) -> None:
    if not _is_aftermarket_session(session):
        return

    current = _current_orderbook_snapshot(quote, "???? ??? ??")
    if current is not None:
        _apply_orderbook_snapshot(quote, current)
        return

    fallback = _regular_close_orderbook_snapshot(quote) or _last_valid_orderbook_snapshot(quote)
    if fallback is not None:
        _apply_orderbook_snapshot(quote, fallback)
        return

    # ??? ? ?? ?? ?? ??? 0?? ????? ???.
    quote["bid_ask_ratio"] = None
    quote["orderbook_display_basis"] = "??? ??? ??"


def _apply_aftermarket_strength_display_policy(quote: dict[str, Any], session: dict[str, Any] | None) -> None:
    if not _is_aftermarket_session(session):
        return

    strength_5m = to_number(quote.get("strength_5m"))
    if strength_5m is not None:
        value = round(max(0.0, min(ONE_MIN_STRENGTH_CAP, float(strength_5m))), 4)
        quote["strength_1m"] = value
        quote["one_min_strength"] = value
        quote["one_min_strength_status"] = "aftermarket_5m"
        quote["strength_display_basis"] = "5? ???? ??"
        return

    held = to_number(
        quote.get("regular_close_strength_1m")
        or quote.get("strength_1m")
        or quote.get("one_min_strength")
        or quote.get("execution_strength")
    )
    if held is not None:
        value = round(max(0.0, min(ONE_MIN_STRENGTH_CAP, float(held))), 4)
        quote["strength_1m"] = value
        quote["one_min_strength"] = value
        quote["one_min_strength_status"] = "regular_close_hold"
        quote["strength_display_basis"] = "??? ??? ??"


def _runtime_context_payload() -> dict[str, Any]:
    market_supply = _load_json_first([RUNTIME_DIR / "market_supply.json", ROOT / "data" / "runtime" / "market_supply.json", ROOT / "docs" / "assets" / "market_supply_snapshot.json", ROOT / "docs" / "assets" / "stockboard_market_supply.json"])
    us_market = _load_json_first([RUNTIME_DIR / "us_market.json", ROOT / "data" / "runtime" / "us_market.json", ROOT / "docs" / "assets" / "us_market_snapshot.json", ROOT / "docs" / "assets" / "stockboard_us_market.json"])
    ohlc_snapshot = _load_json_first([_ohlc_snapshot_path()])
    return {"schema_version": 1, "ts": now_text(), "market_supply": market_supply or {}, "us_market": us_market or {}, "ohlc_snapshot_status": {"count": (ohlc_snapshot or {}).get("count"), "ts": (ohlc_snapshot or {}).get("ts"), "source": (ohlc_snapshot or {}).get("source")}, "source_policy": "read_only_snapshot_files_no_realtime_pipeline_work"}


def _candidate_models_payload() -> dict[str, Any]:
    registry_path = ROOT / "configs" / "candidate_models" / "_registry.json"
    payload = _load_json_first([registry_path]) or {"models": []}
    payload.setdefault("schema_version", 1)
    payload.setdefault("source", str(registry_path))
    return payload


def _guarded_load_universe(self) -> None:
    self.seed_by_code = {}
    self.prev_trade_value_cache_by_code = _load_previous_trade_value_cache()
    self.ohlc_by_code = {}
    self.ohlc_snapshot_mtime = None
    self.ohlc_snapshot_last_check = 0.0
    _load_ohlc_snapshot_if_needed(self, force=True)
    try:
        payload = json.loads(self.universe_file.read_text(encoding="utf-8-sig"))
        built_at = payload.get("built_at") if isinstance(payload, dict) else None
        universe_count = 0
        previous_value_count = 0
        for item in payload.get("items", []) or []:
            code = normalize_code(item.get("stock_code"))
            if not code:
                continue
            universe_count += 1
            self.name_by_code[code] = str(item.get("stock_name") or code)
            self.seed_rank_by_code[code] = int(item.get("seed_rank") or item.get("original_rank") or 999999)
            previous_rank = to_int(item.get("prev_rank"))
            previous_value = to_number(item.get("prev_trade_value_eok"))
            if previous_value is None or previous_value <= 0:
                previous_value = to_number(self.prev_trade_value_cache_by_code.get(code, {}).get("prev_trade_value_eok"))
            if previous_rank is not None and previous_rank > 0:
                self.prev_rank_by_code[code] = previous_rank
            if previous_value is not None and previous_value > 0:
                self.prev_trade_value_by_code[code] = float(previous_value)
                previous_value_count += 1
            self.seed_by_code[code] = {"seed_price": item.get("seed_price"), "seed_change_rate": item.get("seed_change_rate"), "seed_trade_value_eok": item.get("seed_trade_value_eok"), "seed_built_at": built_at, "prev_trade_value_eok": previous_value, "prev_trade_value_source": item.get("prev_trade_value_source"), "prev_trade_value_status": item.get("prev_trade_value_status"), "prev_trade_value_date": item.get("prev_trade_value_date"), "prev_trade_value_lookup_source": item.get("prev_trade_value_lookup_source")}
        _update_market_session_status(self)
        for code in list(self.seed_rank_by_code):
            self._quote(code)
        self.status["universe_seed_quote_count"] = len(self.quotes)
        self.status["universe_seed_built_at"] = built_at
        self.status["universe_count"] = universe_count
        self.status["universe_prev_trade_value_count"] = previous_value_count
        self.status["previous_trade_value_cache_count"] = len(self.prev_trade_value_cache_by_code)
    except Exception as error:
        self.status["last_error"] = f"universe load failed: {error}"


def _guarded_quote(self, code: str) -> dict[str, Any]:
    code = normalize_code(code)
    quote = self.quotes.get(code)
    if quote is not None:
        _copy_previous_fields(self, code, quote, getattr(self, "seed_by_code", {}).get(code, {}) or {})
        _apply_ohlc_snapshot_to_quote(self, code, quote)
        _apply_strength_snapshot_to_quote(self, code, quote)
        return quote
    persisted = self.daily_values_by_code.get(code) or {}
    seed = getattr(self, "seed_by_code", {}).get(code, {}) or {}
    quote = {"stock_code": code, "stock_name": self.name_by_code.get(code, code), "seed_rank": self.seed_rank_by_code.get(code, 999999), "prev_rank": self.prev_rank_by_code.get(code), "large_trade_buy_count": persisted.get("large_trade_buy_count", 0), "large_trade_sell_count": persisted.get("large_trade_sell_count", 0), "large_trade_net_count": persisted.get("large_trade_net_count", 0), "large_trade_buy_sum_eok": persisted.get("large_trade_buy_sum_eok", 0.0), "large_trade_sell_sum_eok": persisted.get("large_trade_sell_sum_eok", 0.0), "large_trade_net_sum_eok": persisted.get("large_trade_net_sum_eok", 0.0), "source_code": "seed_universe", "row_source": "seed_universe"}
    _copy_previous_fields(self, code, quote, seed)
    seed_price = normalized_price(seed.get("seed_price"))
    seed_rate = normalized_rate(seed.get("seed_change_rate"))
    seed_value = to_number(seed.get("seed_trade_value_eok"))
    if seed_price is not None:
        quote["price"] = seed_price
        quote["seed_price"] = seed_price
    if seed_rate is not None:
        quote["change_rate"] = seed_rate
        quote["seed_change_rate"] = seed_rate
    if seed_value is not None:
        quote["trade_value_eok"] = round(float(seed_value), 4)
        quote["seed_trade_value_eok"] = round(float(seed_value), 4)
    if seed.get("seed_built_at"):
        quote["seed_built_at"] = seed.get("seed_built_at")
    for key in ("program_net", "program_net_updated_at", "program_net_source", "program_net_status"):
        if key in persisted:
            quote[key] = persisted.get(key)
    _restore_persisted_live_metrics(quote, persisted)
    _apply_ohlc_snapshot_to_quote(self, code, quote)
    _apply_strength_snapshot_to_quote(self, code, quote)
    if quote.get("ohlc") is None and seed_price is not None:
        _update_intraday_ohlc(quote, seed_price)
    self.quotes[code] = quote
    return quote


def _guarded_rows(self, limit: int = 300) -> list[dict[str, Any]]:
    with self.lock:
        session = _update_market_session_status(self)
        _load_ohlc_snapshot_if_needed(self)
        _load_strength_snapshot_if_needed(self)
        for code in list(self.seed_rank_by_code):
            self._quote(code)
        rows = [deepcopy(row) for row in self.quotes.values()]
    for row in rows:
        if row.get("received_at"):
            row["price_age_sec"] = event_age_sec(row.get("received_at"))
        elif row.get("price") is not None:
            row["price_age_sec"] = None
        _copy_previous_fields(self, row.get("stock_code"), row, getattr(self, "seed_by_code", {}).get(row.get("stock_code"), {}) or {})
        _apply_previous_daily_display_fallback(self, row, session)
        _apply_aftermarket_strength_display_policy(row, session)
        _apply_aftermarket_orderbook_display_policy(row, session)
    rows.sort(key=lambda row: (-(to_number(row.get("trade_value_eok")) or 0), row.get("seed_rank") or 999999, row.get("stock_code") or ""))
    amount_ratio_ready_count = 0
    amount_ratio_missing_count = 0
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        prev_rank = to_int(row.get("prev_rank"))
        row["rank_change"] = (prev_rank - rank) if prev_rank is not None else None
        previous_amount = to_number(row.get("prev_trade_value_eok"))
        current_amount = to_number(row.get("trade_value_eok"))
        if previous_amount is not None and previous_amount > 0 and current_amount is not None:
            row["amount_ratio"] = round(current_amount / previous_amount, 4)
            amount_ratio_ready_count += 1
        else:
            row["amount_ratio"] = None
            amount_ratio_missing_count += 1
            row["amount_ratio_missing_reason"] = "prev_trade_value_missing" if previous_amount is None or previous_amount <= 0 else "current_trade_value_missing"
    try:
        rows = enrich_net_buy_strength_v02_fields(rows)
        self.status["candidate_model_id"] = "NET_BUY_STRENGTH_V02"
        self.status["candidate_grade_count"] = len(rows)
        self.status["candidate_grade_last_error"] = None
    except Exception as error:
        self.status["candidate_grade_last_error"] = str(error)
    self.status["amount_ratio_ready_count"] = amount_ratio_ready_count
    self.status["amount_ratio_missing_count"] = amount_ratio_missing_count
    return rows[:limit]


def _drop_trade(state, quote: dict[str, Any], code: str, reason: str, event: dict[str, Any], values: dict[str, Any], trade_time: str, lag_sec: float | None) -> None:
    state.status["dropped_trade_count"] = int(state.status.get("dropped_trade_count") or 0) + 1
    state.status["last_dropped_trade"] = {"stock_code": code, "reason": reason, "event_ts": event.get("ts"), "trade_time": trade_time, "fid20_lag_sec": lag_sec, "source_code": values.get("source_code") or values.get("registered_code")}
    quote["last_dropped_trade_reason"] = reason
    quote["last_dropped_trade_at"] = now_text()


def _mark_lag_warning(state, quote: dict[str, Any], code: str, values: dict[str, Any], trade_time: str, lag_sec: float | None) -> None:
    if lag_sec is None or lag_sec <= STALE_LAG_WARN_SEC:
        return
    state.status["lagged_trade_warning_count"] = int(state.status.get("lagged_trade_warning_count") or 0) + 1
    state.status["last_lagged_trade_warning"] = {"stock_code": code, "trade_time": trade_time, "fid20_lag_sec": lag_sec, "source_code": values.get("source_code") or values.get("registered_code")}
    quote["fid20_lag_sec"] = lag_sec
    quote["last_lagged_trade_warning_at"] = now_text()


def _guarded_apply_trade(self, event: dict[str, Any]) -> None:
    values = base.merged_event_values(event)
    raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
    code = normalize_code(event.get("stock_code") or event.get("received_code") or values.get("stock_code") or values.get("normalized_code") or values.get("received_code"))
    if not code:
        return
    session = _update_market_session_status(self)
    quote = self._quote(code)
    price = normalized_price(raw.get("price_raw") or values.get("price") or values.get("trade_price") or values.get("realtime_price"))
    change_rate = normalized_rate(raw.get("change_rate_raw") or values.get("change_rate") or values.get("realtime_change_rate"))
    trade_qty = to_int(raw.get("trade_qty_raw") or values.get("trade_qty"))
    flow_buy_qty = to_int(values.get("collector_buy_qty"))
    flow_sell_qty = to_int(values.get("collector_sell_qty"))
    cumulative_volume = to_int(raw.get("cumulative_volume_raw") or values.get("cumulative_volume"))
    trade_value_eok = to_number(values.get("trade_value_eok")) if values.get("trade_value_eok") not in (None, "") else None
    if trade_value_eok is None:
        trade_value_eok = normalized_trade_value_eok(raw.get("cumulative_value_raw") or values.get("cumulative_value"))
    if trade_value_eok is not None:
        trade_value_eok = round(float(trade_value_eok), 4)
    strength = to_number(raw.get("execution_strength_raw") or values.get("execution_strength"))
    trade_time_raw = raw.get("trade_time_raw") or values.get("fid20_trade_time") or values.get("trade_time")
    trade_time = normalize_trade_time(trade_time_raw) or str(trade_time_raw or "")
    trade_time_sec = _time_seconds(trade_time_raw)
    fid20_lag_sec = _lag_seconds(trade_time_sec)
    received_at = event.get("ts") or values.get("price_received_at") or values.get("trade_received_at") or values.get("received_at") or now_text()
    previous_time_sec = quote.get("_trade_time_seconds")
    previous_value = to_number(quote.get("trade_value_eok"))
    previous_volume = to_int(quote.get("cumulative_volume"))
    has_accepted_realtime = quote.get("row_source") == "realtime"
    drop_reason = None
    if session.get("accept_realtime") is False and has_accepted_realtime:
        drop_reason = "market_session_closed"
    if drop_reason is None and has_accepted_realtime and trade_time_sec is not None and previous_time_sec is not None:
        try:
            if int(trade_time_sec) < int(previous_time_sec):
                drop_reason = "older_fid20_than_last_accepted"
        except (TypeError, ValueError):
            pass
    if drop_reason is None and has_accepted_realtime and trade_value_eok is not None and previous_value is not None and float(trade_value_eok) + 1.0 < float(previous_value):
        drop_reason = "cumulative_trade_value_decreased"
    if drop_reason is None and has_accepted_realtime and cumulative_volume is not None and previous_volume is not None and int(cumulative_volume) < int(previous_volume):
        drop_reason = "cumulative_volume_decreased"
    if drop_reason is not None:
        _drop_trade(self, quote, code, drop_reason, event, values, trade_time, fid20_lag_sec)
        return
    _mark_lag_warning(self, quote, code, values, trade_time, fid20_lag_sec)
    if price is not None:
        quote["price"] = price
        quote["trade_price"] = price
        _update_intraday_ohlc(quote, price)
    if change_rate is not None:
        quote["change_rate"] = change_rate
    if trade_qty is not None:
        quote["trade_qty"] = trade_qty
    _apply_strength_snapshot_to_quote(self, code, quote)
    if _is_aftermarket_session(session):
        _apply_aftermarket_strength_display_policy(quote, session)
    else:
        if flow_buy_qty is not None or flow_sell_qty is not None:
            _update_one_min_strength_flow(quote, buy_qty=flow_buy_qty or 0, sell_qty=flow_sell_qty or 0)
        elif trade_qty is not None:
            _update_one_min_strength_flow(quote, buy_qty=max(0, trade_qty), sell_qty=abs(min(0, trade_qty)))
        if _is_regular_session(session) and to_number(quote.get("strength_1m")) is not None:
            quote["regular_close_strength_1m"] = quote.get("strength_1m")
            quote["strength_display_basis"] = "1??? ??"
    if cumulative_volume is not None:
        quote["cumulative_volume"] = cumulative_volume
    if trade_value_eok is not None:
        quote["trade_value_eok"] = trade_value_eok
    if strength is not None:
        quote["execution_strength"] = round(strength, 4)
        quote["execution_strength_updated_at"] = received_at
    _persist_live_metrics(self, code, quote, "execution_strength", "execution_strength_updated_at", "strength_1m", "one_min_buy_qty", "one_min_sell_qty", "one_min_strength_updated_at", "regular_close_strength_1m", "strength_5m", "strength_20m", "strength_60m", "strength_source", "strength_snapshot_at", "strength_status", "strength_display_basis")
    if price is not None:
        _persist_live_metrics(self, code, quote, "day_open", "day_high", "day_low", "day_close", "ohlc")
    quote["trade_time"] = trade_time
    if trade_time_sec is not None:
        quote["_trade_time_seconds"] = trade_time_sec
    quote["fid20_lag_sec"] = fid20_lag_sec
    quote["received_at"] = received_at
    quote["price_age_sec"] = event_age_sec(received_at)
    quote["market_type_raw"] = raw.get("market_type_raw") or values.get("market_type")
    quote["source_code"] = values.get("source_code") or values.get("registered_code")
    quote["row_source"] = "realtime"
    quote.pop("last_dropped_trade_reason", None)
    self.status["trade_count"] += 1
    if trade_qty and price:
        trade_amount = abs(trade_qty) * price
        if trade_amount >= LARGE_TRADE_THRESHOLD_KRW:
            eok = trade_amount / 100_000_000
            if trade_qty > 0:
                quote["large_trade_buy_count"] += 1
                quote["large_trade_buy_sum_eok"] = round(quote["large_trade_buy_sum_eok"] + eok, 4)
            elif trade_qty < 0:
                quote["large_trade_sell_count"] += 1
                quote["large_trade_sell_sum_eok"] = round(quote["large_trade_sell_sum_eok"] + eok, 4)
            quote["large_trade_net_count"] = quote["large_trade_buy_count"] - quote["large_trade_sell_count"]
            quote["large_trade_net_sum_eok"] = round(quote["large_trade_buy_sum_eok"] - quote["large_trade_sell_sum_eok"], 4)
            daily_entry = self.daily_values_by_code.setdefault(code, {})
            for key in base.DAILY_PERSIST_KEYS:
                if key.startswith("large_trade_"):
                    daily_entry[key] = quote.get(key)
            self._mark_daily_dirty()


_original_apply_orderbook = base.State._apply_orderbook


def _guarded_apply_orderbook(self, event: dict[str, Any]) -> None:
    _original_apply_orderbook(self, event)
    values = base.merged_event_values(event)
    code = normalize_code(event.get("stock_code") or event.get("received_code") or values.get("stock_code") or values.get("normalized_code") or values.get("received_code"))
    if not code:
        return
    quote = self.quotes.get(code)
    if not isinstance(quote, dict):
        return
    session = _update_market_session_status(self)
    _remember_last_valid_orderbook_display(quote)
    _remember_regular_orderbook_display(quote, session)
    _apply_aftermarket_orderbook_display_policy(quote, session)
    _persist_live_metrics(self, code, quote, "ask_volume", "bid_volume", "ask_pct", "bid_pct", "bid_ask_ratio", "best_ask_price", "best_bid_price", "orderbook_received_at", "regular_close_bid_ask_ratio", "regular_close_bid_pct", "regular_close_ask_pct", "regular_close_bid_volume", "regular_close_ask_volume", "regular_close_orderbook_at", "orderbook_display_basis", "last_valid_bid_ask_ratio", "last_valid_bid_pct", "last_valid_ask_pct", "last_valid_bid_volume", "last_valid_ask_volume", "last_valid_orderbook_at")


_original_snapshot = base.State.snapshot


def _guarded_snapshot(self, limit: int = 300) -> dict[str, Any]:
    session = _update_market_session_status(self)
    payload = _original_snapshot(self, limit)
    payload["market_session"] = session
    if isinstance(payload.get("status"), dict):
        payload["status"].update({"market_phase": session.get("phase"), "market_phase_label": session.get("phase_label"), "market_trading_date": session.get("trading_date"), "market_accept_realtime": session.get("accept_realtime"), "market_freeze_realtime_missing": session.get("freeze_realtime_missing"), "market_session_reason": session.get("reason")})
    return payload


_original_do_get = base.WebHandler.do_GET


def _guarded_do_GET(self) -> None:
    parsed = base.urlparse(self.path)
    if parsed.path == "/api/v2/candidate_models":
        self._json(_candidate_models_payload())
        return
    if parsed.path == "/api/v2/context":
        self._json(_runtime_context_payload())
        return
    _original_do_get(self)


base.State._load_universe = _guarded_load_universe
base.State._quote = _guarded_quote
base.State.rows = _guarded_rows
base.State._apply_trade = _guarded_apply_trade
base.State._apply_orderbook = _guarded_apply_orderbook
base.State.snapshot = _guarded_snapshot
base.WebHandler.do_GET = _guarded_do_GET

if __name__ == "__main__":
    raise SystemExit(base.main())
