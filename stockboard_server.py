import json
import os
import subprocess
import sys
import time
from collections import deque
from copy import deepcopy
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, urlparse

from kiwoom_data_provider import (
    KiwoomOpenApiRealtimeProvider, fetch_foreign_investor_net_after_close,
    fetch_foreign_sum, fetch_market_supply, fetch_ohlc, fetch_program_net,
    fetch_trade_value_top100, issue_access_token,
)
from stockboard_engine import (
    KST,
    _stock_code,
    _program_net_enabled,
    _query_date,
    _request_sleep_sec,
    build_top100_filter_report,
    enrich_candidate_fields,
    prepare_display_rows,
)
from stockboard_store import RealtimeStore, _load_tradable_stock_codes


DOCS_DIR = Path(__file__).resolve().parent / "docs"
HOST = os.getenv("KIWOOM_HOST", "127.0.0.1")
PORT = int(os.getenv("KIWOOM_PORT", "8000"))
REGULAR_CLOSE_START_HOUR = 15
REGULAR_CLOSE_START_MINUTE = 30
AFTERMARKET_START_HOUR = 15
AFTERMARKET_START_MINUTE = 40
AFTERMARKET_REALTIME_FRESH_SEC = 60.0
HOT_LANE_CANDIDATE_LIMIT = 5
PRICE_LIGHT_TOP_LIMIT = int(os.getenv("STOCKBOARD_PRICE_LIGHT_TOP_LIMIT", "100"))
PRICE_LIGHT_MIN_INTERVAL_SEC = float(
    os.getenv("STOCKBOARD_PRICE_LIGHT_MIN_INTERVAL_SEC", "0.25")
)
PRICE_LIGHT_MAX_CONSECUTIVE_SKIPS = int(
    os.getenv("STOCKBOARD_PRICE_LIGHT_MAX_CONSECUTIVE_SKIPS", "3")
)
CANDIDATE_SAFE_PRICE_MAX_AGE_SEC = float(
    os.getenv("STOCKBOARD_CANDIDATE_SAFE_PRICE_MAX_AGE_SEC", "5.0")
)
CANDIDATE_SAFE_ORDERBOOK_MAX_AGE_SEC = float(
    os.getenv("STOCKBOARD_CANDIDATE_SAFE_ORDERBOOK_MAX_AGE_SEC", "15.0")
)
CANDIDATE_SAFE_BACKOFF_MS = int(
    os.getenv("STOCKBOARD_CANDIDATE_SAFE_BACKOFF_MS", "1000")
)
ADAPTIVE_SPEED_ENABLED = (
    os.getenv("STOCKBOARD_ADAPTIVE_SPEED_ENABLED", "1").strip().lower()
    not in {"0", "false", "no", "off"}
)
ADAPTIVE_OPENING_BIAS_START_MINUTE = 9 * 60
ADAPTIVE_OPENING_BIAS_END_MINUTE = 9 * 60 + 3
ADAPTIVE_HOT_PRICE_MAX_AGE_SEC = float(
    os.getenv("STOCKBOARD_ADAPTIVE_HOT_PRICE_MAX_AGE_SEC", "1.5")
)
ADAPTIVE_HOT_ORDERBOOK_MAX_AGE_SEC = float(
    os.getenv("STOCKBOARD_ADAPTIVE_HOT_ORDERBOOK_MAX_AGE_SEC", "3.0")
)
ADAPTIVE_HOT_API_LATENCY_MAX_MS = float(
    os.getenv("STOCKBOARD_ADAPTIVE_HOT_API_LATENCY_MAX_MS", "250")
)
ADAPTIVE_RECOVERY_REQUIRED_OK = int(
    os.getenv("STOCKBOARD_ADAPTIVE_RECOVERY_REQUIRED_OK", "3")
)
ADAPTIVE_NORMAL_REQUIRED_OK = int(
    os.getenv("STOCKBOARD_ADAPTIVE_NORMAL_REQUIRED_OK", "6")
)
ADAPTIVE_RECOVERY_INTERVAL_MULTIPLIER = float(
    os.getenv("STOCKBOARD_ADAPTIVE_RECOVERY_INTERVAL_MULTIPLIER", "2.0")
)
ADAPTIVE_PENDING_GAP_MIN = int(
    os.getenv("STOCKBOARD_ADAPTIVE_PENDING_GAP_MIN", "3")
)
ADAPTIVE_PENDING_GAP_REQUIRED_STREAK = int(
    os.getenv("STOCKBOARD_ADAPTIVE_PENDING_GAP_REQUIRED_STREAK", "2")
)
ADAPTIVE_RESUME_BIAS_SEC = float(
    os.getenv("STOCKBOARD_ADAPTIVE_RESUME_BIAS_SEC", "30")
)
ROLLING_TRADE_API_FIELDS = (
    "one_min_strength",
    "one_min_buy_qty",
    "one_min_sell_qty",
    "big_hand_buy_count_1eok",
    "big_hand_sell_count_1eok",
    "big_hand_net_buy_count_1eok",
    "big_hand_buy_sum_eok",
    "big_hand_sell_sum_eok",
    "big_hand_net_sum_eok",
)


def _listening_pids(host, port):
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"netstat failed while checking {host}:{port}: {result.stderr.strip()}"
        )

    endpoint = f"{host}:{port}".lower()
    pids = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        if fields[1].lower() != endpoint or fields[3].upper() != "LISTENING":
            continue
        try:
            pid = int(fields[4])
            if pid > 0:
                pids.add(pid)
        except ValueError:
            continue
    return pids


def _stop_existing_server(host, port, timeout_seconds=5.0):
    current_pid = os.getpid()
    old_pids = sorted(_listening_pids(host, port) - {current_pid})
    print(f"current pid={current_pid}", flush=True)
    print(f"old server pids={old_pids}", flush=True)

    for pid in old_pids:
        print(f"old stockboard server found pid={pid}", flush=True)
        print("killing old stockboard server...", flush=True)
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode != 0:
            print(
                f"warning: taskkill failed for pid={pid}: {result.stderr.strip()}",
                file=sys.stderr,
                flush=True,
            )

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining_pids = _listening_pids(host, port) - {current_pid}
        if not remaining_pids:
            if old_pids:
                print("old server killed.", flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"port {host}:{port} is still listening on pids "
                f"{sorted(remaining_pids)}"
            )
        time.sleep(0.1)


def _market_session(now=None):
    current = now or datetime.now(KST)
    if current.weekday() >= 5:
        return "장마감"
    minutes = current.hour * 60 + current.minute
    if 8 * 60 <= minutes < 9 * 60:
        return "프리마켓"
    if 9 * 60 <= minutes < 15 * 60 + 30:
        return "정규장"
    if 15 * 60 + 30 <= minutes < 20 * 60:
        return "애프터마켓"
    return "장마감"


def _realtime_stock_codes(rows):
    codes = []
    seen_codes = set()
    for row in rows:
        stock_code = row.get("stock_code") if isinstance(row, dict) else None
        if (
            not isinstance(stock_code, str)
            or len(stock_code) != 6
            or not stock_code.isdigit()
            or stock_code in seen_codes
        ):
            continue
        seen_codes.add(stock_code)
        codes.append(f"{stock_code}_AL")
    return codes


def _apply_foreign_display(rows, market_session):
    use_foreign_investor_net = (
        market_session == "장마감"
        and any(
            isinstance(row.get("foreign_investor_net"), (int, float))
            and not isinstance(row.get("foreign_investor_net"), bool)
            for row in rows
        )
    )
    label = "외인(억)" if use_foreign_investor_net else "외합(억)"
    value_key = "foreign_investor_net" if use_foreign_investor_net else "foreign_sum"
    for row in rows:
        row["foreign_display_label"] = label
        row["foreign_display_value"] = row.get(value_key)
        row["foreign_display_source"] = value_key


def _realtime_number(value):
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _realtime_abs_number(value):
    number = _realtime_number(value)
    return abs(number) if number is not None else None


def _realtime_acc_trade_value_diagnostics(value):
    number = _realtime_number(value)
    if number is None:
        return {
            "raw": None,
            "million": None,
            "eok_candidate": None,
        }
    eok_candidate = number / 100
    return {
        "raw": number,
        "million": number,
        "eok_candidate": int(eok_candidate)
        if eok_candidate == int(eok_candidate)
        else eok_candidate,
    }


def _latest_event(events_by_code, stock_code):
    events = events_by_code.get(stock_code)
    if not events:
        return None
    event = events[-1]
    return event if isinstance(event, dict) else None


def _age_seconds(timestamp_text):
    if not timestamp_text:
        return None
    try:
        timestamp = datetime.fromisoformat(str(timestamp_text).replace("Z", "+00:00"))
        now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
        return max(0.0, round((now - timestamp).total_seconds(), 3))
    except (TypeError, ValueError):
        return None


def _market_clock_phase(now=None):
    current = now or datetime.now(KST)
    regular_close_start = current.replace(
        hour=REGULAR_CLOSE_START_HOUR,
        minute=REGULAR_CLOSE_START_MINUTE,
        second=0,
        microsecond=0,
    )
    aftermarket_start = current.replace(
        hour=AFTERMARKET_START_HOUR,
        minute=AFTERMARKET_START_MINUTE,
        second=0,
        microsecond=0,
    )
    if current < regular_close_start:
        return "regular"
    if current < aftermarket_start:
        return "regular_close_lock"
    return "aftermarket"


def _has_value(value):
    return value is not None and value != ""


def _is_ohlc(value):
    return isinstance(value, dict) and not isinstance(value, list)


def _is_al_source(value):
    return str(value or "").upper().endswith("_AL")


def _price_received_at(row):
    return (
        row.get("price_received_at")
        or row.get("trade_received_at")
        or row.get("realtime_price_received_at")
        or row.get("realtime_trade_received_at")
        or row.get("realtime_received_at")
    )


def _price_age_seconds(row):
    return _age_seconds(_price_received_at(row))


def _apply_freshness_fields(row):
    price_age_sec = _price_age_seconds(row)
    orderbook_age_sec = _age_seconds(row.get("orderbook_received_at"))
    row["price_age_sec"] = price_age_sec
    row["orderbook_age_sec"] = orderbook_age_sec
    row["price_fresh"] = (
        price_age_sec is not None
        and price_age_sec <= AFTERMARKET_REALTIME_FRESH_SEC
    )
    row["price_status"] = "fresh" if row["price_fresh"] else "stale_or_missing"
    return row


def _live_expected_for_gate(now=None):
    return _market_session(now) == "정규장"


def _gate_age_status(age_sec, max_age_sec, live_expected):
    if live_expected:
        if age_sec is None:
            return "bad_missing"
        if age_sec > max_age_sec:
            return "bad_stale"
        return "ok"
    if age_sec is None:
        return "no_live_expected"
    if age_sec > max_age_sec:
        return "inactive_session"
    return "ok"


def _combine_gate_status(price_status, orderbook_status):
    bad_statuses = []
    if price_status.startswith("bad_"):
        bad_statuses.append("bad_price_age")
    if orderbook_status.startswith("bad_"):
        bad_statuses.append("bad_orderbook_age")
    if bad_statuses:
        return "+".join(bad_statuses)
    inactive_statuses = [
        status
        for status in (price_status, orderbook_status)
        if status in {"no_live_expected", "inactive_session"}
    ]
    if inactive_statuses:
        return "+".join(dict.fromkeys(inactive_statuses))
    return "ok"


def _diagnostic_counts_for_mode(diagnostic):
    if not isinstance(diagnostic, dict):
        return False
    if diagnostic.get("live_expected") is False:
        return False
    statuses = {
        diagnostic.get("status"),
        diagnostic.get("price_live_status"),
        diagnostic.get("orderbook_live_status"),
    }
    statuses.discard(None)
    if statuses and all(
        status in {"no_live_expected", "inactive_session"}
        for status in statuses
    ):
        return False
    return True


def _hot_quote_gate_diagnostic(
    stock_code,
    quote,
    max_price_age_sec,
    max_orderbook_age_sec,
    now_dt,
    selected=False,
):
    market_session = _market_session(now_dt)
    live_expected = _live_expected_for_gate(now_dt)
    if not isinstance(quote, dict):
        status = "missing_price_light_quote" if live_expected else "no_live_expected"
        return {
            "diagnostic": {
                "stock_code": stock_code,
                "price_age_sec": None,
                "orderbook_age_sec": None,
                "status": status,
                "price_live_status": status,
                "orderbook_live_status": status,
                "market_session": market_session,
                "live_expected": live_expected,
                "selected": bool(selected),
                "diagnostic_scope": "selected_hot" if selected else "hot_candidate",
            },
            "bad_reasons": ["hot_price_light_missing"] if live_expected else [],
            "price_sequence": 0,
        }

    price_age_sec = _age_seconds(
        quote.get("price_received_at") or quote.get("received_at")
    )
    orderbook_age_sec = _age_seconds(quote.get("orderbook_received_at"))
    price_status = _gate_age_status(
        price_age_sec, max_price_age_sec, live_expected
    )
    orderbook_status = _gate_age_status(
        orderbook_age_sec, max_orderbook_age_sec, live_expected
    )
    bad_reasons = []
    if price_status.startswith("bad_"):
        bad_reasons.append("hot_price_age_bad")
    if orderbook_status.startswith("bad_"):
        bad_reasons.append("hot_orderbook_age_bad")
    try:
        price_sequence = int(quote.get("price_sequence") or 0)
    except (TypeError, ValueError):
        price_sequence = 0
    return {
        "diagnostic": {
            "stock_code": stock_code,
            "price_age_sec": price_age_sec,
            "orderbook_age_sec": orderbook_age_sec,
            "price_sequence": quote.get("price_sequence"),
            "orderbook_sequence": quote.get("orderbook_sequence"),
            "orderbook_source": quote.get("orderbook_source"),
            "realtime_source_code": quote.get("realtime_source_code"),
            "status": _combine_gate_status(price_status, orderbook_status),
            "price_live_status": price_status,
            "orderbook_live_status": orderbook_status,
            "market_session": market_session,
            "live_expected": live_expected,
            "selected": bool(selected),
            "diagnostic_scope": "selected_hot" if selected else "hot_candidate",
        },
        "bad_reasons": bad_reasons,
        "price_sequence": price_sequence,
    }


def _is_realtime_fresh(row):
    age = _price_age_seconds(row)
    return age is not None and age <= AFTERMARKET_REALTIME_FRESH_SEC


def _regular_close_snapshot_from_row(row):
    realtime_ohlc = row.get("realtime_ohlc")
    base_ohlc = row.get("ohlc")
    if (
        _has_value(row.get("realtime_price"))
        and _has_value(row.get("realtime_change_rate"))
        and _is_ohlc(realtime_ohlc)
        and _is_al_source(row.get("realtime_source_code"))
    ):
        return {
            "regular_close_price": row.get("realtime_price"),
            "regular_close_change_rate": row.get("realtime_change_rate"),
            "regular_close_ohlc": deepcopy(realtime_ohlc),
            "regular_close_snapshot_source": "realtime_al",
            "regular_close_snapshot_status": "ok",
        }
    if _has_value(row.get("price")) and _has_value(row.get("change_rate")):
        ohlc = realtime_ohlc if _is_ohlc(realtime_ohlc) else base_ohlc
        if _is_ohlc(ohlc):
            return {
                "regular_close_price": row.get("price"),
                "regular_close_change_rate": row.get("change_rate"),
                "regular_close_ohlc": deepcopy(ohlc),
                "regular_close_snapshot_source": (
                    "price_change_rate_realtime_ohlc"
                    if _is_ohlc(realtime_ohlc)
                    else "price_change_rate_base_ohlc"
                ),
                "regular_close_snapshot_status": "ok",
            }
    if _has_value(row.get("rest_price")) and _is_ohlc(base_ohlc):
        return {
            "regular_close_price": row.get("rest_price"),
            "regular_close_change_rate": row.get("rest_change_rate"),
            "regular_close_ohlc": deepcopy(base_ohlc),
            "regular_close_snapshot_source": "rest_price_base_ohlc",
            "regular_close_snapshot_status": "ok",
        }
    return {
        "regular_close_snapshot_source": "unavailable",
        "regular_close_snapshot_status": "unavailable",
    }


def _copy_regular_close_fields(target, source):
    for field in (
        "regular_close_price",
        "regular_close_change_rate",
        "regular_close_ohlc",
        "regular_close_snapshot_at",
        "regular_close_snapshot_source",
        "regular_close_snapshot_status",
    ):
        if field in source:
            target[field] = deepcopy(source.get(field))


def _apply_display_price_fields(row, now=None):
    phase = _market_clock_phase(now)
    regular_price = row.get("regular_close_price")
    regular_change_rate = row.get("regular_close_change_rate")
    regular_ohlc = row.get("regular_close_ohlc")
    realtime_price = row.get("realtime_price")
    realtime_change_rate = row.get("realtime_change_rate")
    realtime_ohlc = row.get("realtime_ohlc")
    base_price = row.get("price")
    base_change_rate = row.get("change_rate")
    base_ohlc = row.get("ohlc")

    if phase == "regular_close_lock" and _has_value(regular_price):
        row["display_price"] = regular_price
        row["display_change_rate"] = regular_change_rate
        row["display_ohlc"] = deepcopy(regular_ohlc)
        row["price_source"] = "regular_close_snapshot"
        row["display_ohlc_source"] = "regular_close_snapshot"
        return row

    if phase == "aftermarket":
        if _has_value(realtime_price) and _is_realtime_fresh(row):
            row["display_price"] = realtime_price
            row["display_change_rate"] = realtime_change_rate
            row["display_ohlc"] = deepcopy(realtime_ohlc) if _is_ohlc(realtime_ohlc) else None
            row["price_source"] = "aftermarket_realtime"
            row["display_ohlc_source"] = (
                "realtime_ohlc" if _is_ohlc(realtime_ohlc) else "unavailable"
            )
            return row
        if _has_value(regular_price):
            row["display_price"] = regular_price
            row["display_change_rate"] = regular_change_rate
            row["display_ohlc"] = deepcopy(regular_ohlc)
            row["price_source"] = "regular_close_snapshot_fallback"
            row["display_ohlc_source"] = "regular_close_snapshot"
            return row

    if _has_value(realtime_price):
        row["display_price"] = realtime_price
        row["display_change_rate"] = realtime_change_rate
        row["display_ohlc"] = deepcopy(realtime_ohlc) if _is_ohlc(realtime_ohlc) else None
        row["price_source"] = "realtime"
        row["display_ohlc_source"] = (
            "realtime_ohlc" if _is_ohlc(realtime_ohlc) else "unavailable"
        )
    elif _has_value(base_price):
        row["display_price"] = base_price
        row["display_change_rate"] = base_change_rate
        row["display_ohlc"] = deepcopy(base_ohlc) if _is_ohlc(base_ohlc) else None
        row["price_source"] = "close_snapshot_candidate"
        row["display_ohlc_source"] = "base_ohlc" if _is_ohlc(base_ohlc) else "unavailable"
    else:
        row["display_price"] = None
        row["display_change_rate"] = None
        row["display_ohlc"] = None
        row["price_source"] = "unavailable"
        row["display_ohlc_source"] = "unavailable"
    return row


def _sanitize_realtime_display_patch(patch):
    if patch.get("price_source") != "unavailable":
        return patch
    if _has_value(patch.get("display_price")) or _has_value(
        patch.get("display_change_rate")
    ):
        return patch
    for field in (
        "display_price",
        "display_change_rate",
        "display_ohlc",
        "price_source",
        "display_ohlc_source",
    ):
        patch.pop(field, None)
    patch["display_patch_status"] = "unavailable_omitted"
    return patch


def _close_metric_codes(rows):
    candidates = []
    top_codes = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        stock_code = row.get("stock_code")
        if not isinstance(stock_code, str) or stock_code in seen:
            continue
        seen.add(stock_code)
        if row.get("is_candidate") or row.get("candidate_rank"):
            candidates.append(stock_code)
        top_codes.append(stock_code)
    return list(dict.fromkeys(candidates[:5] + top_codes[:20]))


def _parse_codes_query(query):
    return [
        code.strip()
        for value in query.get("codes", [])
        for code in value.split(",")
        if code.strip()
    ]


def _query_flag_enabled(query, *names):
    enabled_values = {"1", "true", "yes", "on", "debug"}
    for name in names:
        for value in query.get(name, []):
            if str(value).strip().lower() in enabled_values:
                return True
    return False


def _parse_hot_code_query(query):
    codes = []
    for key in ("selected", "selected_codes", "hot_codes", "codes", "code"):
        for value in query.get(key, []):
            codes.extend(
                code.strip()
                for code in str(value).split(",")
                if code.strip()
            )
    normalized = []
    for code in codes:
        stock_code = _stock_code(code)
        if stock_code is not None:
            normalized.append(stock_code)
    return list(dict.fromkeys(normalized))


def _hot_candidate_codes(rows, limit=HOT_LANE_CANDIDATE_LIMIT):
    enriched_rows = enrich_candidate_fields(deepcopy(rows))
    candidate_rows = [
        row for row in enriched_rows if isinstance(row.get("candidate_rank"), int)
    ]
    candidate_rows.sort(key=lambda row: row.get("candidate_rank"))
    return [
        row["stock_code"]
        for row in candidate_rows[:limit]
        if _stock_code(row.get("stock_code")) is not None
    ]


def _hot_lane_codes(rows, selected_codes=None):
    selected = [
        code
        for code in (_stock_code(raw_code) for raw_code in (selected_codes or []))
        if code is not None
    ]
    return list(dict.fromkeys(_hot_candidate_codes(rows) + selected))


def _price_light_codes(rows, limit=PRICE_LIGHT_TOP_LIMIT):
    codes = []
    seen_codes = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        stock_code = _stock_code(row.get("stock_code"))
        if stock_code is None or stock_code in seen_codes:
            continue
        seen_codes.add(stock_code)
        codes.append(stock_code)
        if limit > 0 and len(codes) >= limit:
            break
    return codes


def _overlay_close_metrics(row, snapshot, include_debug=False):
    if not isinstance(snapshot, dict):
        return
    fields = [
        "bid_pct",
        "ask_pct",
        "bid_volume_snapshot",
        "ask_volume_snapshot",
        "bid_ask_ratio_snapshot",
        "orderbook_snapshot_at",
        "orderbook_stale_sec",
        "orderbook_status",
        "orderbook_error",
        "orderbook_status_detail",
        "orderbook_requested_at",
        "orderbook_completed_at",
        "orderbook_tr_repeat_count",
        "orderbook_rqname",
        "orderbook_trcode",
        "orderbook_screen_no",
        "realtime_strength_snapshot",
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "strength_source",
        "strength_snapshot_at",
        "strength_stale_sec",
        "strength_status",
        "strength_error",
        "strength_status_detail",
    ]
    if include_debug:
        fields.extend(
            [
                "orderbook_raw_sample",
                "strength_raw_sample",
                "strength_requested_at",
                "strength_completed_at",
                "strength_tr_repeat_count",
                "strength_rqname",
                "strength_trcode",
                "strength_screen_no",
            ]
        )
    for field in fields:
        if field in snapshot:
            row[field] = snapshot.get(field)
    if row.get("orderbook_source") is None:
        row["orderbook_source"] = snapshot.get("orderbook_source")
    if row.get("bid_volume") is None:
        row["bid_volume"] = snapshot.get("bid_volume_snapshot")
    if row.get("ask_volume") is None:
        row["ask_volume"] = snapshot.get("ask_volume_snapshot")
    if row.get("bid_ask_ratio") is None:
        row["bid_ask_ratio"] = snapshot.get("bid_ask_ratio_snapshot")
    if row.get("realtime_strength") is None:
        row["realtime_strength_snapshot"] = snapshot.get(
            "realtime_strength_snapshot"
        )


def _overlay_quote_latest(row, quote):
    if not isinstance(quote, dict):
        return
    realtime_price = _realtime_abs_number(quote.get("price"))
    realtime_change_rate = _realtime_number(quote.get("change_rate"))
    row["realtime_price"] = realtime_price
    row["realtime_change_rate"] = realtime_change_rate
    row["realtime_trade_time"] = quote.get("trade_time")
    row["realtime_strength"] = _realtime_number(
        quote.get("execution_strength")
    )
    row["realtime_acc_volume"] = _realtime_number(
        quote.get("cumulative_volume")
    )
    row["realtime_acc_trade_value"] = _realtime_number(
        quote.get("cumulative_value")
    )
    trade_value_diagnostics = _realtime_acc_trade_value_diagnostics(
        quote.get("cumulative_value")
    )
    row["realtime_acc_trade_value_raw"] = trade_value_diagnostics["raw"]
    row["realtime_acc_trade_value_million"] = trade_value_diagnostics["million"]
    row["realtime_acc_trade_value_eok_candidate"] = (
        trade_value_diagnostics["eok_candidate"]
    )
    row["session_strength"] = _realtime_number(quote.get("session_strength"))
    row["session_buy_qty_live"] = _realtime_number(
        quote.get("session_buy_qty_live")
    )
    row["session_sell_qty_live"] = _realtime_number(
        quote.get("session_sell_qty_live")
    )
    row["session_strength_source"] = quote.get("session_strength_source")
    for field in ROLLING_TRADE_API_FIELDS:
        row[field] = _realtime_number(quote.get(field))
    for field in (
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "strength_snapshot_at",
        "strength_source",
        "strength_stale_sec",
        "strength_status",
    ):
        if field in quote:
            row[field] = quote.get(field)
    realtime_ohlc = quote.get("realtime_ohlc")
    if realtime_ohlc is not None:
        row["realtime_ohlc"] = deepcopy(realtime_ohlc)
        row["realtime_ohlc_source"] = quote.get("realtime_ohlc_source")
    row["realtime_received_at"] = quote.get("received_at")
    row["price_received_at"] = (
        quote.get("price_received_at")
        or quote.get("trade_received_at")
        or quote.get("received_at")
    )
    row["trade_received_at"] = (
        quote.get("trade_received_at")
        or quote.get("price_received_at")
        or quote.get("received_at")
    )
    row["price_sequence"] = quote.get("price_sequence") or quote.get("sequence")
    row["trade_sequence"] = quote.get("trade_sequence") or quote.get("sequence")
    row["trade_lag_sec"] = quote.get("trade_lag_sec")
    row["fid20_trade_lag_sec"] = (
        quote.get("fid20_trade_lag_sec") or quote.get("trade_lag_sec")
    )
    row["stale_trade_suspect"] = quote.get("stale_trade_suspect")
    row["realtime_received_code"] = quote.get("received_code")
    row["realtime_registered_code"] = quote.get("registered_code")
    row["realtime_source_code"] = quote.get("realtime_source_code") or quote.get(
        "source_code"
    )
    row["realtime_source"] = "tick"
    row["realtime_lane"] = "slow_latest"
    row["realtime_is_stale"] = False
    if realtime_price is not None:
        row["price"] = realtime_price
    if realtime_change_rate is not None:
        row["change_rate"] = realtime_change_rate

    row["bid_volume"] = _realtime_number(
        quote.get("bid_volume")
        if "bid_volume" in quote
        else quote.get("total_bid_qty")
    )
    row["ask_volume"] = _realtime_number(
        quote.get("ask_volume")
        if "ask_volume" in quote
        else quote.get("total_ask_qty")
    )
    row["bid_ask_ratio"] = _realtime_number(quote.get("bid_ask_ratio"))
    row["best_bid_price"] = _realtime_abs_number(quote.get("best_bid"))
    row["best_ask_price"] = _realtime_abs_number(quote.get("best_ask"))
    row["orderbook_received_at"] = quote.get("orderbook_received_at")
    row["orderbook_age_sec"] = _age_seconds(
        quote.get("orderbook_received_at") or quote.get("received_at")
    )
    row["orderbook_source"] = quote.get("orderbook_source")
    row["orderbook_sequence"] = quote.get("orderbook_sequence")
    if (
        quote.get("orderbook_status") is not None
        or row.get("orderbook_received_at") is not None
        or row.get("bid_volume") is not None
        or row.get("ask_volume") is not None
    ):
        row["orderbook_status"] = quote.get("orderbook_status") or "ok"


def _top100_with_realtime(rows, realtime_store, include_debug=False):
    response_rows = [deepcopy(row) for row in rows]
    codes = [row.get("stock_code") for row in response_rows]
    try:
        snapshot = realtime_store.snapshot_latest_many(codes)
    except Exception as error:
        print(
            f"warning: realtime overlay skipped for /api/top100: {error}",
            file=sys.stderr,
            flush=True,
        )
        snapshot = {"quotes": {}, "close_metrics": {}}

    quotes = snapshot.get("quotes", {})
    close_metrics = snapshot.get("close_metrics", {})
    for row in response_rows:
        stock_code = row.get("stock_code")
        row["rest_price"] = row.get("price")
        row["rest_change_rate"] = row.get("change_rate")
        _overlay_close_metrics(
            row,
            close_metrics.get(stock_code),
            include_debug=include_debug,
        )
        _overlay_quote_latest(row, quotes.get(stock_code))
        if _market_clock_phase() != "regular":
            try:
                regular_snapshot = realtime_store.ensure_regular_close_snapshot(
                    stock_code,
                    _regular_close_snapshot_from_row(row),
                )
                _copy_regular_close_fields(row, regular_snapshot)
            except Exception as error:
                row["regular_close_snapshot_status"] = "error"
                row["regular_close_snapshot_error"] = str(error)
        _apply_freshness_fields(row)
        _apply_display_price_fields(row)
    return response_rows


def _parse_price_light_since(query):
    for key in ("since_price_sequence", "since_sequence"):
        if key not in query:
            continue
        try:
            value = int(query[key][0])
            if value < 0:
                raise ValueError
            return value, False, None
        except (TypeError, ValueError):
            return None, True, f"invalid_{key}"
    return None, False, None


def _price_light_patch_payload(
    realtime_store,
    stock_codes,
    since_price_sequence=None,
    fallback=False,
    fallback_reason=None,
    skipped=False,
    skip_reason=None,
    retry_after_ms=None,
):
    mode = "full"
    try:
        snapshot = realtime_store.snapshot_price_light(
            stock_codes,
            None if fallback or since_price_sequence is None else since_price_sequence,
        )
        if not fallback and since_price_sequence is not None:
            mode = "delta"
    except Exception as error:
        snapshot = realtime_store.snapshot_price_light(stock_codes)
        fallback = True
        fallback_reason = str(error) or "price_light_delta_failed"
    rows = []
    if not skipped:
        for stock_code, quote in snapshot.get("quotes", {}).items():
            price = _realtime_abs_number(quote.get("price"))
            change_rate = _realtime_number(quote.get("change_rate"))
            row = {
                "stock_code": stock_code,
                "price": price,
                "change_rate": change_rate,
                "realtime_price": price,
                "realtime_change_rate": change_rate,
                "price_received_at": quote.get("price_received_at"),
                "price_updated_at": quote.get("price_updated_at"),
                "price_sequence": quote.get("price_sequence"),
                "received_code": quote.get("received_code"),
                "normalized_code": quote.get("normalized_code"),
                "registered_code": quote.get("registered_code"),
                "original_registered_code": quote.get("original_registered_code"),
                "realtime_source_code": quote.get("realtime_source_code"),
                "source_code": quote.get("source_code"),
                "price_age_sec": _age_seconds(
                    quote.get("price_received_at") or quote.get("received_at")
                ),
            }
            rows.append(row)
    return {
        "sequence": snapshot.get("sequence"),
        "price_sequence": snapshot.get("price_sequence"),
        "updated_at": snapshot.get("updated_at"),
        "mode": mode,
        "lane": "price_light",
        "priority": "below_hot",
        "fields": ["price", "change_rate"],
        "since_price_sequence": since_price_sequence,
        "fallback": bool(fallback),
        "fallback_reason": fallback_reason,
        "skipped": bool(skipped),
        "skip_reason": skip_reason,
        "retry_after_ms": retry_after_ms,
        "requested_codes": list(stock_codes) if stock_codes is not None else None,
        "rows": rows,
    }


def _price_light_candidate_rows(rows, price_light_snapshot):
    quotes = price_light_snapshot.get("quotes", {})
    response_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stock_code = _stock_code(row.get("stock_code"))
        if stock_code is None:
            continue
        quote = quotes.get(stock_code)
        if not isinstance(quote, dict):
            continue
        preview_row = deepcopy(row)
        price = _realtime_abs_number(quote.get("price"))
        change_rate = _realtime_number(quote.get("change_rate"))
        preview_row["rest_price"] = preview_row.get("price")
        preview_row["rest_change_rate"] = preview_row.get("change_rate")
        preview_row["price"] = price
        preview_row["change_rate"] = change_rate
        preview_row["realtime_price"] = price
        preview_row["realtime_change_rate"] = change_rate
        preview_row["price_received_at"] = quote.get("price_received_at")
        preview_row["price_updated_at"] = quote.get("price_updated_at")
        preview_row["price_sequence"] = quote.get("price_sequence")
        preview_row["received_code"] = quote.get("received_code")
        preview_row["normalized_code"] = quote.get("normalized_code")
        preview_row["registered_code"] = quote.get("registered_code")
        preview_row["original_registered_code"] = quote.get(
            "original_registered_code"
        )
        preview_row["realtime_source_code"] = quote.get("realtime_source_code")
        preview_row["source_code"] = quote.get("source_code")
        preview_row["bid_volume"] = _realtime_number(quote.get("bid_volume"))
        preview_row["ask_volume"] = _realtime_number(quote.get("ask_volume"))
        preview_row["bid_ask_ratio"] = _realtime_number(
            quote.get("bid_ask_ratio")
        )
        preview_row["orderbook_received_at"] = quote.get("orderbook_received_at")
        preview_row["orderbook_updated_at"] = quote.get("orderbook_updated_at")
        preview_row["orderbook_sequence"] = quote.get("orderbook_sequence")
        preview_row["orderbook_source"] = quote.get("orderbook_source")
        preview_row["candidate_lane"] = "safe_price_light_preview"
        _apply_freshness_fields(preview_row)
        response_rows.append(preview_row)
    return response_rows


def _candidate_rows_only(rows, limit=HOT_LANE_CANDIDATE_LIMIT):
    candidate_rows = [
        row for row in rows if isinstance(row.get("candidate_rank"), int)
    ]
    candidate_rows.sort(key=lambda row: row.get("candidate_rank"))
    return candidate_rows[:limit]


def _candidate_safe_lane_payload(
    rows,
    price_light_snapshot,
    hot_codes,
    gate,
    limit=HOT_LANE_CANDIDATE_LIMIT,
):
    candidate_rows = []
    if gate["allowed"]:
        preview_rows = _price_light_candidate_rows(rows, price_light_snapshot)
        candidate_rows = _candidate_rows_only(
            enrich_candidate_fields(preview_rows), limit=limit
        )
    return {
        "lane": "candidate_safe_ranking",
        "purpose": "preview_diagnostic",
        "source": "store_price_light_snapshot",
        "candidate_limit": limit,
        "sequence": price_light_snapshot.get("sequence"),
        "price_sequence": price_light_snapshot.get("price_sequence"),
        "updated_at": price_light_snapshot.get("updated_at"),
        "skipped": not gate["allowed"],
        "skip_reason": gate["skip_reason"],
        "retry_after_ms": gate["retry_after_ms"],
        "gate": gate,
        "hot_codes": list(hot_codes),
        "candidate_codes": [
            row.get("stock_code")
            for row in candidate_rows
            if _stock_code(row.get("stock_code")) is not None
        ],
        "rows": candidate_rows,
    }


def _realtime_patch_payload(
    realtime_store,
    since_sequence=None,
    fallback=False,
    fallback_reason=None,
    stock_codes=None,
    lane="realtime_patch",
):
    mode = "full"
    if fallback or since_sequence is None:
        snapshot = realtime_store.snapshot_quotes_only(stock_codes)
    else:
        try:
            snapshot = realtime_store.snapshot_quotes_since(
                since_sequence, stock_codes
            )
            mode = "delta"
        except Exception as error:
            snapshot = realtime_store.snapshot_quotes_only(stock_codes)
            fallback = True
            fallback_reason = str(error) or "delta_failed"
    patches = []
    max_price_sequence = 0
    for stock_code, quote in snapshot.get("quotes", {}).items():
        price = _realtime_abs_number(quote.get("price"))
        change_rate = _realtime_number(quote.get("change_rate"))
        best_bid = _realtime_abs_number(quote.get("best_bid"))
        best_ask = _realtime_abs_number(quote.get("best_ask"))
        trade_value_diagnostics = _realtime_acc_trade_value_diagnostics(
            quote.get("cumulative_value")
        )
        realtime_ohlc = quote.get("realtime_ohlc")
        patch = {
            "stock_code": stock_code,
            "price": price,
            "change_rate": change_rate,
            "realtime_price": price,
            "realtime_change_rate": change_rate,
            "trade_time": quote.get("trade_time"),
            "fid20_trade_time": quote.get("trade_time"),
            "trade_lag_sec": quote.get("trade_lag_sec"),
            "fid20_trade_lag_sec": (
                quote.get("fid20_trade_lag_sec") or quote.get("trade_lag_sec")
            ),
            "stale_trade_suspect": quote.get("stale_trade_suspect"),
            "price_received_at": quote.get("price_received_at"),
            "trade_received_at": quote.get("trade_received_at"),
            "price_sequence": quote.get("price_sequence"),
            "trade_sequence": quote.get("trade_sequence"),
            "received_code": quote.get("received_code"),
            "normalized_code": quote.get("normalized_code"),
            "registered_code": quote.get("registered_code"),
            "original_registered_code": quote.get("original_registered_code"),
            "realtime_source_code": quote.get("realtime_source_code"),
            "source_code": quote.get("source_code"),
            "realtime_strength": _realtime_number(
                quote.get("execution_strength")
            ),
            "realtime_acc_volume": _realtime_number(
                quote.get("cumulative_volume")
            ),
            "realtime_acc_trade_value": _realtime_number(
                quote.get("cumulative_value")
            ),
            "realtime_acc_trade_value_raw": trade_value_diagnostics["raw"],
            "realtime_acc_trade_value_million": trade_value_diagnostics[
                "million"
            ],
            "realtime_acc_trade_value_eok_candidate": (
                trade_value_diagnostics["eok_candidate"]
            ),
            "session_strength": _realtime_number(quote.get("session_strength")),
            "session_buy_qty_live": _realtime_number(
                quote.get("session_buy_qty_live")
            ),
            "session_sell_qty_live": _realtime_number(
                quote.get("session_sell_qty_live")
            ),
            "session_strength_source": quote.get("session_strength_source"),
            "one_min_strength": _realtime_number(quote.get("one_min_strength")),
            "one_min_buy_qty": _realtime_number(quote.get("one_min_buy_qty")),
            "one_min_sell_qty": _realtime_number(quote.get("one_min_sell_qty")),
            "big_hand_buy_count_1eok": _realtime_number(
                quote.get("big_hand_buy_count_1eok")
            ),
            "big_hand_sell_count_1eok": _realtime_number(
                quote.get("big_hand_sell_count_1eok")
            ),
            "big_hand_net_buy_count_1eok": _realtime_number(
                quote.get("big_hand_net_buy_count_1eok")
            ),
            "big_hand_buy_sum_eok": _realtime_number(
                quote.get("big_hand_buy_sum_eok")
            ),
            "big_hand_sell_sum_eok": _realtime_number(
                quote.get("big_hand_sell_sum_eok")
            ),
            "big_hand_net_sum_eok": _realtime_number(
                quote.get("big_hand_net_sum_eok")
            ),
            "realtime_strength_snapshot": _realtime_number(
                quote.get("realtime_strength_snapshot")
            ),
            "strength_5m": _realtime_number(quote.get("strength_5m")),
            "strength_20m": _realtime_number(quote.get("strength_20m")),
            "strength_60m": _realtime_number(quote.get("strength_60m")),
            "strength_snapshot_at": quote.get("strength_snapshot_at"),
            "strength_source": quote.get("strength_source"),
            "strength_stale_sec": _realtime_number(
                quote.get("strength_stale_sec")
            ),
            "strength_status": quote.get("strength_status"),
            "realtime_ohlc": (
                deepcopy(realtime_ohlc) if realtime_ohlc is not None else None
            ),
            "realtime_ohlc_source": quote.get("realtime_ohlc_source"),
            "bid_volume": _realtime_number(
                quote.get("bid_volume")
                if "bid_volume" in quote
                else quote.get("total_bid_qty")
            ),
            "ask_volume": _realtime_number(
                quote.get("ask_volume")
                if "ask_volume" in quote
                else quote.get("total_ask_qty")
            ),
            "bid_volume_snapshot": _realtime_number(
                quote.get("bid_volume_snapshot")
            ),
            "ask_volume_snapshot": _realtime_number(
                quote.get("ask_volume_snapshot")
            ),
            "bid_pct": _realtime_number(quote.get("bid_pct")),
            "ask_pct": _realtime_number(quote.get("ask_pct")),
            "bid_ask_ratio": _realtime_number(quote.get("bid_ask_ratio")),
            "bid_ask_ratio_snapshot": _realtime_number(
                quote.get("bid_ask_ratio_snapshot")
            ),
            "best_bid_price": best_bid,
            "best_ask_price": best_ask,
            "orderbook_received_at": quote.get("orderbook_received_at"),
            "orderbook_sequence": quote.get("orderbook_sequence"),
            "orderbook_snapshot_at": quote.get("orderbook_snapshot_at"),
            "orderbook_age_sec": _age_seconds(
                quote.get("orderbook_received_at")
            ),
            "orderbook_stale_sec": _realtime_number(
                quote.get("orderbook_stale_sec")
            ),
            "orderbook_source": quote.get("orderbook_source"),
            "orderbook_status": quote.get("orderbook_status"),
            "orderbook_error": quote.get("orderbook_error"),
            "orderbook_status_detail": quote.get("orderbook_status_detail"),
            "received_at": quote.get("received_at"),
            "sequence": quote.get("sequence"),
        }
        _copy_regular_close_fields(patch, quote)
        _apply_freshness_fields(patch)
        _apply_display_price_fields(patch)
        _sanitize_realtime_display_patch(patch)
        patches.append(patch)
        try:
            max_price_sequence = max(
                max_price_sequence,
                int(patch.get("price_sequence") or 0),
            )
        except (TypeError, ValueError):
            pass
    return {
        "sequence": snapshot.get("sequence"),
        "price_sequence": max_price_sequence,
        "updated_at": snapshot.get("updated_at"),
        "mode": mode,
        "lane": lane,
        "since_sequence": since_sequence,
        "fallback": bool(fallback),
        "fallback_reason": fallback_reason,
        "requested_codes": list(stock_codes) if stock_codes is not None else None,
        "rows": patches,
    }


REALTIME_SLIM_QUOTE_FIELDS = frozenset(
    {
        "stock_code",
        "stock_name",
        "price",
        "change_rate",
        "trade_price",
        "trade_qty",
        "trade_side",
        "trade_time",
        "fid20_trade_time",
        "trade_lag_sec",
        "fid20_trade_lag_sec",
        "stale_trade_suspect",
        "execution_strength",
        "cumulative_volume",
        "cumulative_value",
        "price_received_at",
        "price_updated_at",
        "price_sequence",
        "trade_received_at",
        "trade_updated_at",
        "trade_sequence",
        "received_code",
        "normalized_code",
        "registered_code",
        "original_registered_code",
        "realtime_source_code",
        "source_code",
        "received_at",
        "updated_at",
        "sequence",
        "best_bid",
        "best_ask",
        "bid_volume",
        "ask_volume",
        "total_bid_qty",
        "total_ask_qty",
        "bid_ask_ratio",
        "orderbook_received_at",
        "orderbook_updated_at",
        "orderbook_sequence",
        "orderbook_source",
        "orderbook_status",
        "orderbook_error",
        "orderbook_status_detail",
        "orderbook_snapshot_at",
        "orderbook_stale_sec",
        "realtime_strength_snapshot",
        "session_buy_qty_live",
        "session_sell_qty_live",
        "session_strength",
        "session_strength_source",
        "one_min_strength",
        "one_min_buy_qty",
        "one_min_sell_qty",
        "big_hand_buy_count_1eok",
        "big_hand_sell_count_1eok",
        "big_hand_net_buy_count_1eok",
        "big_hand_buy_sum_eok",
        "big_hand_sell_sum_eok",
        "big_hand_net_sum_eok",
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "strength_snapshot_at",
        "strength_source",
        "strength_stale_sec",
        "strength_status",
        "realtime_ohlc",
        "realtime_ohlc_source",
        "regular_close_price",
        "regular_close_change_rate",
        "regular_close_ohlc",
        "regular_close_snapshot_at",
        "regular_close_snapshot_source",
        "regular_close_snapshot_status",
        "latest_only_enabled",
        "latest_only_dropped_count",
        "latest_only_dropped_at",
        "latest_only_dropped_reason",
    }
)

REALTIME_SLIM_CLOSE_METRIC_FIELDS = frozenset(
    {
        "stock_code",
        "bid_volume_snapshot",
        "ask_volume_snapshot",
        "bid_pct",
        "ask_pct",
        "bid_ask_ratio_snapshot",
        "orderbook_source",
        "orderbook_snapshot_at",
        "orderbook_status",
        "orderbook_error",
        "orderbook_status_detail",
        "orderbook_stale_sec",
        "realtime_strength_snapshot",
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "strength_source",
        "strength_snapshot_at",
        "strength_status",
        "strength_error",
        "strength_status_detail",
        "strength_stale_sec",
        "updated_at",
    }
)


def _slim_close_metrics(metrics):
    if not isinstance(metrics, dict):
        return {}
    return {
        code: {
            field: deepcopy(value)
            for field, value in snapshot.items()
            if field in REALTIME_SLIM_CLOSE_METRIC_FIELDS
        }
        for code, snapshot in metrics.items()
        if isinstance(snapshot, dict)
    }


def _slim_realtime_quote(stock_code, quote):
    if not isinstance(quote, dict):
        return {}
    slim = {
        field: deepcopy(value)
        for field, value in quote.items()
        if field in REALTIME_SLIM_QUOTE_FIELDS
    }
    slim["stock_code"] = slim.get("stock_code") or stock_code
    price = _realtime_abs_number(slim.get("price"))
    change_rate = _realtime_number(slim.get("change_rate"))
    trade_value_diagnostics = _realtime_acc_trade_value_diagnostics(
        slim.get("cumulative_value")
    )
    slim["price"] = price
    slim["change_rate"] = change_rate
    slim["realtime_price"] = price
    slim["realtime_change_rate"] = change_rate
    slim["trade_value_eok"] = trade_value_diagnostics["eok_candidate"]
    slim["cumulative_value_eok"] = trade_value_diagnostics["eok_candidate"]
    slim["realtime_acc_trade_value"] = _realtime_number(
        slim.get("cumulative_value")
    )
    slim["realtime_acc_trade_value_eok_candidate"] = (
        trade_value_diagnostics["eok_candidate"]
    )
    slim["realtime_strength"] = _realtime_number(
        slim.get("execution_strength")
    )
    if slim.get("fid20_trade_time") is None:
        slim["fid20_trade_time"] = slim.get("trade_time")
    if slim.get("fid20_trade_lag_sec") is None:
        slim["fid20_trade_lag_sec"] = slim.get("trade_lag_sec")
    if slim.get("price_received_at") is None:
        slim["price_received_at"] = (
            slim.get("trade_received_at") or slim.get("received_at")
        )
    if slim.get("trade_received_at") is None:
        slim["trade_received_at"] = (
            slim.get("price_received_at") or slim.get("received_at")
        )
    best_bid = _realtime_abs_number(slim.get("best_bid"))
    best_ask = _realtime_abs_number(slim.get("best_ask"))
    slim["best_bid"] = best_bid
    slim["best_ask"] = best_ask
    slim["best_bid_price"] = best_bid
    slim["best_ask_price"] = best_ask
    _apply_freshness_fields(slim)
    _apply_display_price_fields(slim)
    return slim


def _realtime_snapshot_payload(snapshot, include_debug=False):
    payload = deepcopy(snapshot)
    for quote in payload.get("quotes", {}).values():
        if not isinstance(quote, dict):
            continue
        if quote.get("fid20_trade_time") is None:
            quote["fid20_trade_time"] = quote.get("trade_time")
        if quote.get("fid20_trade_lag_sec") is None:
            quote["fid20_trade_lag_sec"] = quote.get("trade_lag_sec")
        _apply_freshness_fields(quote)
    if include_debug:
        payload["payload_mode"] = "debug"
        return payload
    return {
        "sequence": payload.get("sequence"),
        "updated_at": payload.get("updated_at"),
        "payload_mode": "slim",
        "quotes": {
            stock_code: _slim_realtime_quote(stock_code, quote)
            for stock_code, quote in payload.get("quotes", {}).items()
            if isinstance(quote, dict)
        },
        "close_metrics": _slim_close_metrics(payload.get("close_metrics", {})),
    }


def _empty_foreign_investor_net_data(query_date, error=None):
    return {
        "values": {},
        "stats": {
            "available": False,
            "query_date": query_date,
            "market_counts": {"KOSPI": 0, "KOSDAQ": 0},
            "market_stats": [],
            "collected_count": 0,
            "joined_count": 0,
            "errors": (
                [{"market": None, "error": str(error)}]
                if error is not None
                else []
            ),
            "rate_limit": None,
            "raw_samples": [],
            "converted_samples": [],
            "request_sleep_seconds": None,
        },
    }


def make_handler(
    rows,
    filter_report_rows,
    expected_row_count,
    expected_ohlc_count,
    expected_rows_id,
    market_supply,
    realtime_store,
    realtime_provider,
    realtime_provider_error,
    realtime_provider_start_requested,
    realtime_provider_start_succeeded,
    realtime_provider_register_requested,
    realtime_provider_register_succeeded,
    realtime_provider_registered_count,
    realtime_provider_register_error,
):
    selected_hot_lock = RLock()
    selected_hot_codes = set(
        _parse_hot_code_query(
            {"codes": [os.getenv("STOCKBOARD_HOT_SELECTED_CODES", "")]}
        )
    )
    price_light_lock = RLock()
    price_light_last_served_at = 0.0
    price_light_consecutive_skips = 0
    hot_lane_last_served_price_sequence = 0
    adaptive_speed_state = {
        "mode": "normal",
        "reason": "init",
        "ok_streak": 0,
        "bad_streak": 0,
        "pending_gap_streak": 0,
        "pending_gap": 0,
        "pending_gap_strong": False,
        "health_reasons": [],
        "bias_reason": None,
        "resume_bias_until_monotonic": None,
        "last_resume_signal": None,
        "last_changed_at": datetime.now(KST).isoformat(timespec="seconds"),
        "hot_api_latency_samples_ms": deque(maxlen=12),
        "last_hot_api_latency_ms": None,
        "last_evaluated_at": None,
    }

    def selected_hot_snapshot():
        with selected_hot_lock:
            return sorted(selected_hot_codes)

    def set_selected_hot_codes(codes):
        normalized_codes = {
            code
            for code in (_stock_code(raw_code) for raw_code in (codes or []))
            if code is not None
        }
        with selected_hot_lock:
            selected_hot_codes.clear()
            selected_hot_codes.update(normalized_codes)
            selected = sorted(selected_hot_codes)
        if realtime_provider is not None and hasattr(
            realtime_provider, "set_hot_priority_codes"
        ):
            try:
                realtime_provider.set_hot_priority_codes(
                    _hot_lane_codes(rows, selected)
                )
            except Exception as error:
                print(
                    f"warning: hot priority update skipped: {error}",
                    file=sys.stderr,
                    flush=True,
                )
        return selected

    def current_hot_lane_codes(query_selected=None):
        selected = selected_hot_snapshot()
        if query_selected:
            selected = list(dict.fromkeys(selected + list(query_selected)))
        return _hot_lane_codes(rows, selected)

    def record_hot_api_latency(latency_ms):
        try:
            latency_ms = round(float(latency_ms), 3)
        except (TypeError, ValueError):
            return
        with price_light_lock:
            adaptive_speed_state["last_hot_api_latency_ms"] = latency_ms
            adaptive_speed_state["hot_api_latency_samples_ms"].append(latency_ms)

    def adaptive_speed_snapshot():
        with price_light_lock:
            samples = list(adaptive_speed_state["hot_api_latency_samples_ms"])
            return {
                "enabled": ADAPTIVE_SPEED_ENABLED,
                "profile": "price-lane",
                "mode": adaptive_speed_state["mode"],
                "reason": adaptive_speed_state["reason"],
                "ok_streak": adaptive_speed_state["ok_streak"],
                "bad_streak": adaptive_speed_state["bad_streak"],
                "pending_gap_streak": adaptive_speed_state.get(
                    "pending_gap_streak", 0
                ),
                "pending_gap": adaptive_speed_state.get("pending_gap", 0),
                "pending_gap_strong": adaptive_speed_state.get(
                    "pending_gap_strong", False
                ),
                "health_reasons": list(
                    adaptive_speed_state.get("health_reasons") or []
                ),
                "bias_reason": adaptive_speed_state.get("bias_reason"),
                "last_changed_at": adaptive_speed_state["last_changed_at"],
                "last_evaluated_at": adaptive_speed_state["last_evaluated_at"],
                "last_hot_api_latency_ms": (
                    adaptive_speed_state["last_hot_api_latency_ms"]
                ),
                "hot_api_latency_avg_ms": (
                    round(sum(samples) / len(samples), 3) if samples else None
                ),
                "hot_diagnostics": list(
                    adaptive_speed_state.get("hot_diagnostics") or []
                ),
                "hot_price_sequence": adaptive_speed_state.get(
                    "hot_price_sequence"
                ),
                "hot_last_served_price_sequence": adaptive_speed_state.get(
                    "hot_last_served_price_sequence"
                ),
                "opening_bias": adaptive_speed_state.get("opening_bias"),
                "resume_bias": adaptive_speed_state.get("resume_bias"),
                "thresholds": {
                    "opening_bias_window": "09:00~09:03",
                    "opening_bias_policy": "low_start_until_hot_health_recovers",
                    "hot_price_max_age_sec": ADAPTIVE_HOT_PRICE_MAX_AGE_SEC,
                    "hot_orderbook_max_age_sec": (
                        ADAPTIVE_HOT_ORDERBOOK_MAX_AGE_SEC
                    ),
                    "hot_api_latency_max_ms": ADAPTIVE_HOT_API_LATENCY_MAX_MS,
                    "recovery_required_ok": ADAPTIVE_RECOVERY_REQUIRED_OK,
                    "normal_required_ok": ADAPTIVE_NORMAL_REQUIRED_OK,
                    "recovery_interval_multiplier": (
                        ADAPTIVE_RECOVERY_INTERVAL_MULTIPLIER
                    ),
                    "pending_gap_min": ADAPTIVE_PENDING_GAP_MIN,
                    "pending_gap_required_streak": (
                        ADAPTIVE_PENDING_GAP_REQUIRED_STREAK
                    ),
                    "resume_bias_sec": ADAPTIVE_RESUME_BIAS_SEC,
                },
            }

    def _set_adaptive_mode_locked(mode, reason):
        previous = adaptive_speed_state["mode"]
        adaptive_speed_state["mode"] = mode
        adaptive_speed_state["reason"] = reason
        if mode != previous:
            adaptive_speed_state["last_changed_at"] = datetime.now(KST).isoformat(
                timespec="seconds"
            )

    def _adaptive_opening_bias(now_dt):
        minute = now_dt.hour * 60 + now_dt.minute
        return {
            "active": (
                ADAPTIVE_OPENING_BIAS_START_MINUTE
                <= minute
                < ADAPTIVE_OPENING_BIAS_END_MINUTE
            ),
            "window": "09:00~09:03",
            "policy": "low_start_until_hot_health_recovers",
        }

    def _resume_bias_signal(provider_status):
        if not isinstance(provider_status, dict):
            return None
        signal_fields = (
            "market_interrupt_signal",
            "market_resume_signal",
            "trading_resume_signal",
            "sidecar_signal",
            "circuit_breaker_signal",
            "market_operation_signal",
        )
        text_parts = []
        for field in signal_fields:
            value = provider_status.get(field)
            if value:
                text_parts.append(f"{field}={value}")
        for field in (
            "market_state",
            "market_status",
            "market_operation_state",
            "fid215_state",
            "fid215",
        ):
            value = provider_status.get(field)
            if value:
                text_parts.append(f"{field}={value}")
        text = " ".join(str(part).lower() for part in text_parts)
        if not text:
            return None
        keywords = (
            "resume",
            "reopen",
            "sidecar",
            "circuit",
            "breaker",
            "halt",
            "suspend",
            "거래재개",
            "사이드카",
            "서킷",
            "중단",
            "재개",
        )
        if not any(keyword in text for keyword in keywords):
            return None
        return text[:240]

    def _adaptive_resume_bias(now_dt, provider_status):
        now_mono = time.monotonic()
        signal = _resume_bias_signal(provider_status)
        with price_light_lock:
            if signal and signal != adaptive_speed_state.get("last_resume_signal"):
                adaptive_speed_state["last_resume_signal"] = signal
                adaptive_speed_state["resume_bias_until_monotonic"] = (
                    now_mono + max(0.0, ADAPTIVE_RESUME_BIAS_SEC)
                )
            until = adaptive_speed_state.get("resume_bias_until_monotonic")
        active = until is not None and now_mono < until
        return {
            "active": active,
            "window_sec": ADAPTIVE_RESUME_BIAS_SEC,
            "signal": signal,
            "until_monotonic": until,
            "market_session": _market_session(now_dt),
        }

    def evaluate_adaptive_speed(hot_codes, price_light_snapshot=None):
        if not ADAPTIVE_SPEED_ENABLED:
            return adaptive_speed_snapshot()
        now_dt = datetime.now(KST)
        opening_bias = _adaptive_opening_bias(now_dt)
        live_expected = _live_expected_for_gate(now_dt)
        provider_status = None
        provider_hot_refresh_pending = False
        if realtime_provider is not None:
            try:
                provider_status = realtime_provider.status()
                provider_hot_refresh_pending = bool(
                    provider_status.get("orderbook_hot_refresh_pending")
                )
            except Exception:
                provider_hot_refresh_pending = True
        resume_bias = _adaptive_resume_bias(now_dt, provider_status)

        snapshot = price_light_snapshot
        if snapshot is None:
            try:
                snapshot = realtime_store.snapshot_price_light(hot_codes)
            except Exception:
                snapshot = {"quotes": {}}
        hot_quotes = snapshot.get("quotes", {}) if isinstance(snapshot, dict) else {}
        bad_reasons = []
        diagnostics = []
        try:
            snapshot_price_sequence = int(snapshot.get("price_sequence") or 0)
        except (AttributeError, TypeError, ValueError):
            snapshot_price_sequence = 0
        with price_light_lock:
            last_hot_sequence = hot_lane_last_served_price_sequence
            previous_pending_streak = adaptive_speed_state.get(
                "pending_gap_streak", 0
            )
        pending_gap = (
            max(0, snapshot_price_sequence - last_hot_sequence)
            if last_hot_sequence > 0
            else 0
        )
        pending_gap_streak = (
            previous_pending_streak + 1
            if pending_gap >= max(1, ADAPTIVE_PENDING_GAP_MIN)
            else 0
        )
        pending_gap_strong = (
            pending_gap_streak >= max(1, ADAPTIVE_PENDING_GAP_REQUIRED_STREAK)
        )
        if pending_gap_strong:
            bad_reasons.append("hot_pending_gap_accumulated")
        selected_codes = set(selected_hot_snapshot())
        for stock_code in hot_codes:
            quote = hot_quotes.get(stock_code)
            gate_result = _hot_quote_gate_diagnostic(
                stock_code,
                quote,
                ADAPTIVE_HOT_PRICE_MAX_AGE_SEC,
                ADAPTIVE_HOT_ORDERBOOK_MAX_AGE_SEC,
                now_dt,
                selected=stock_code in selected_codes,
            )
            diagnostic = gate_result["diagnostic"]
            diagnostics.append(diagnostic)
            if _diagnostic_counts_for_mode(diagnostic):
                bad_reasons.extend(
                    "hot_price_age_high"
                    if reason == "hot_price_age_bad"
                    else "hot_orderbook_age_high"
                    if reason == "hot_orderbook_age_bad"
                    else "hot_quote_missing"
                    if reason == "hot_price_light_missing"
                    else reason
                    for reason in gate_result["bad_reasons"]
                )
        if provider_hot_refresh_pending and live_expected:
            bad_reasons.append("hot_orderbook_refresh_pending")
        with price_light_lock:
            samples = list(adaptive_speed_state["hot_api_latency_samples_ms"])
            latency_avg = sum(samples) / len(samples) if samples else 0.0
            if samples and latency_avg > ADAPTIVE_HOT_API_LATENCY_MAX_MS:
                bad_reasons.append("hot_api_latency_high")
            unique_bad_reasons = list(dict.fromkeys(bad_reasons))
            adaptive_speed_state["last_evaluated_at"] = now_dt.isoformat(
                timespec="seconds"
            )
            adaptive_speed_state["hot_diagnostics"] = diagnostics
            adaptive_speed_state["hot_api_latency_avg_ms"] = (
                round(latency_avg, 3) if samples else None
            )
            adaptive_speed_state["hot_price_sequence"] = snapshot_price_sequence
            adaptive_speed_state["hot_last_served_price_sequence"] = (
                last_hot_sequence
            )
            adaptive_speed_state["pending_gap"] = pending_gap
            adaptive_speed_state["pending_gap_streak"] = pending_gap_streak
            adaptive_speed_state["pending_gap_strong"] = pending_gap_strong
            adaptive_speed_state["health_reasons"] = unique_bad_reasons
            adaptive_speed_state["opening_bias"] = opening_bias
            adaptive_speed_state["resume_bias"] = resume_bias
            adaptive_speed_state["bias_reason"] = None
            if unique_bad_reasons:
                adaptive_speed_state["ok_streak"] = 0
                adaptive_speed_state["bad_streak"] += 1
                reason = unique_bad_reasons[0]
                mode = (
                    "protect"
                    if reason
                    in {"hot_api_latency_high", "hot_pending_gap_accumulated"}
                    else "degraded"
                )
                _set_adaptive_mode_locked(mode, reason)
            else:
                adaptive_speed_state["bad_streak"] = 0
                adaptive_speed_state["ok_streak"] += 1
                required_recovery = max(1, ADAPTIVE_RECOVERY_REQUIRED_OK)
                required_normal = max(required_recovery, ADAPTIVE_NORMAL_REQUIRED_OK)
                ok_streak = adaptive_speed_state["ok_streak"]
                bias_reason = None
                if opening_bias.get("active"):
                    bias_reason = "opening_bias"
                if resume_bias.get("active"):
                    bias_reason = "resume_bias"
                adaptive_speed_state["bias_reason"] = bias_reason
                if ok_streak >= required_normal:
                    _set_adaptive_mode_locked("normal", "hot_lane_stable")
                elif ok_streak >= required_recovery:
                    _set_adaptive_mode_locked("recovery", "hot_lane_recovering")
                elif bias_reason:
                    _set_adaptive_mode_locked("degraded", bias_reason)
        return adaptive_speed_snapshot()

    def note_hot_lane_served(payload):
        nonlocal hot_lane_last_served_price_sequence
        price_sequence = payload.get("price_sequence")
        if price_sequence is None:
            price_sequence = 0
            for row in payload.get("rows", []):
                try:
                    price_sequence = max(
                        price_sequence,
                        int(row.get("price_sequence") or 0),
                    )
                except (TypeError, ValueError):
                    continue
        try:
            price_sequence = int(price_sequence or 0)
        except (TypeError, ValueError):
            price_sequence = 0
        with price_light_lock:
            hot_lane_last_served_price_sequence = max(
                hot_lane_last_served_price_sequence,
                price_sequence,
            )

    def price_light_gate(hot_codes):
        nonlocal price_light_consecutive_skips
        now = time.monotonic()
        with price_light_lock:
            elapsed = now - price_light_last_served_at
            last_hot_sequence = hot_lane_last_served_price_sequence
            skip_count = price_light_consecutive_skips
        adaptive = evaluate_adaptive_speed(hot_codes)
        min_interval = max(0.0, PRICE_LIGHT_MIN_INTERVAL_SEC)
        if adaptive["mode"] == "recovery":
            min_interval *= max(1.0, ADAPTIVE_RECOVERY_INTERVAL_MULTIPLIER)
        retry_after_ms = None
        reason = None
        hard_skip = False
        if adaptive["mode"] in {"protect", "degraded"}:
            reason = f"adaptive_{adaptive['mode']}"
            retry_after_ms = max(1, int(max(min_interval, 0.5) * 1000))
            hard_skip = adaptive["mode"] == "protect"
        elif elapsed < min_interval:
            retry_after_ms = max(1, int((min_interval - elapsed) * 1000))
            reason = "price_light_min_interval"
        try:
            hot_snapshot = realtime_store.snapshot_price_light(hot_codes)
            hot_pending_sequence = int(hot_snapshot.get("price_sequence") or 0)
        except Exception:
            hot_pending_sequence = last_hot_sequence
        pending_gap = (
            max(0, hot_pending_sequence - last_hot_sequence)
            if last_hot_sequence > 0
            else 0
        )
        if (
            pending_gap >= max(1, ADAPTIVE_PENDING_GAP_MIN)
            and adaptive.get("pending_gap_strong")
        ):
            reason = "hot_lane_pending_delta"
            retry_after_ms = retry_after_ms or int(min_interval * 1000)
        if realtime_provider is not None:
            try:
                provider_status = realtime_provider.status()
                if (
                    provider_status.get("orderbook_hot_refresh_pending")
                    and _live_expected_for_gate()
                ):
                    reason = "hot_lane_refresh_pending"
                    retry_after_ms = retry_after_ms or int(min_interval * 1000)
            except Exception:
                pass
        max_skips = max(0, PRICE_LIGHT_MAX_CONSECUTIVE_SKIPS)
        if reason and (hard_skip or max_skips == 0 or skip_count < max_skips):
            with price_light_lock:
                price_light_consecutive_skips += 1
            return {
                "allowed": False,
                "skip_reason": reason,
                "retry_after_ms": retry_after_ms,
                "hot_price_sequence": hot_pending_sequence,
                "hot_last_served_price_sequence": last_hot_sequence,
                "adaptive_speed": adaptive,
            }
        with price_light_lock:
            price_light_consecutive_skips = 0
        return {
            "allowed": True,
            "skip_reason": None,
            "retry_after_ms": None,
            "hot_price_sequence": hot_pending_sequence,
            "hot_last_served_price_sequence": last_hot_sequence,
            "adaptive_speed": adaptive,
        }

    def candidate_safe_gate(hot_codes, price_light_snapshot):
        with price_light_lock:
            last_hot_sequence = hot_lane_last_served_price_sequence
        hot_quotes = price_light_snapshot.get("quotes", {})
        hot_diagnostics = []
        bad_reasons = []
        adaptive = evaluate_adaptive_speed(hot_codes, price_light_snapshot)
        if adaptive["mode"] == "protect":
            bad_reasons.append(f"adaptive_{adaptive['mode']}")
        max_price_age = max(0.0, CANDIDATE_SAFE_PRICE_MAX_AGE_SEC)
        max_orderbook_age = max(0.0, CANDIDATE_SAFE_ORDERBOOK_MAX_AGE_SEC)
        hot_price_sequence = 0
        now_dt = datetime.now(KST)
        live_expected = _live_expected_for_gate(now_dt)
        selected_codes = set(selected_hot_snapshot())
        for stock_code in hot_codes:
            quote = hot_quotes.get(stock_code)
            gate_result = _hot_quote_gate_diagnostic(
                stock_code,
                quote,
                max_price_age,
                max_orderbook_age,
                now_dt,
                selected=stock_code in selected_codes,
            )
            hot_price_sequence = max(
                hot_price_sequence, gate_result["price_sequence"]
            )
            bad_reasons.extend(gate_result["bad_reasons"])
            hot_diagnostics.append(gate_result["diagnostic"])
        hot_patch_pending = (
            last_hot_sequence > 0 and hot_price_sequence > last_hot_sequence
        )
        provider_hot_refresh_pending = False
        provider_hot_refresh_error = None
        if realtime_provider is not None:
            try:
                provider_status = realtime_provider.status()
                provider_hot_refresh_pending = bool(
                    provider_status.get("orderbook_hot_refresh_pending")
                )
                provider_hot_refresh_error = provider_status.get(
                    "orderbook_hot_refresh_error"
                )
            except Exception as error:
                provider_hot_refresh_error = str(error)
        hot_patch_gap = (
            max(0, hot_price_sequence - last_hot_sequence)
            if last_hot_sequence > 0
            else 0
        )
        hot_patch_pending_strong = (
            hot_patch_gap >= max(1, ADAPTIVE_PENDING_GAP_MIN)
            and adaptive.get("pending_gap_strong")
        )
        if hot_patch_pending_strong:
            bad_reasons.append("hot_pending_gap_accumulated")
        if provider_hot_refresh_pending and live_expected:
            bad_reasons.append("hot_orderbook_refresh_pending")
        skip_reason = next(iter(dict.fromkeys(bad_reasons)), None)
        allowed = skip_reason is None
        return {
            "allowed": allowed,
            "skip_reason": skip_reason,
            "retry_after_ms": None if allowed else max(1, CANDIDATE_SAFE_BACKOFF_MS),
            "max_price_age_sec": max_price_age,
            "max_orderbook_age_sec": max_orderbook_age,
            "hot_price_sequence": hot_price_sequence,
            "hot_last_served_price_sequence": last_hot_sequence,
            "hot_patch_pending": hot_patch_pending,
            "hot_patch_gap": hot_patch_gap,
            "hot_patch_pending_strong": hot_patch_pending_strong,
            "orderbook_hot_refresh_pending": provider_hot_refresh_pending,
            "orderbook_hot_refresh_error": provider_hot_refresh_error,
            "hot_diagnostics": hot_diagnostics,
            "adaptive_speed": adaptive,
        }

    def note_price_light_served():
        nonlocal price_light_last_served_at
        with price_light_lock:
            price_light_last_served_at = time.monotonic()

    class RequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

        def do_GET(self):
            parsed_url = urlparse(self.path)
            request_path = parsed_url.path
            query = parse_qs(parsed_url.query, keep_blank_values=True)
            if request_path in ("", "/"):
                self.send_response(302)
                self.send_header("Location", "/stockboard_v0_3_0_sample.html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if request_path == "/api/health":
                response_payload = {
                    "ok": True,
                    "server": "stockboard",
                    "pid": os.getpid(),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "store_available": realtime_store is not None,
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-StockBoard-PID", str(os.getpid()))
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime":
                include_debug = _query_flag_enabled(query, "debug", "include_debug")
                if "codes" in query:
                    codes = [
                        code.strip()
                        for value in query["codes"]
                        for code in value.split(",")
                        if code.strip()
                    ]
                    try:
                        snapshot = (
                            realtime_store.snapshot_many(codes)
                            if include_debug
                            else realtime_store.snapshot_latest_many(codes)
                        )
                        response_payload = _realtime_snapshot_payload(
                            snapshot,
                            include_debug=include_debug,
                        )
                    except ValueError as error:
                        body = json.dumps({"error": str(error)}).encode("utf-8")
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                else:
                    snapshot = (
                        realtime_store.snapshot()
                        if include_debug
                        else realtime_store.snapshot_latest()
                    )
                    response_payload = _realtime_snapshot_payload(
                        snapshot,
                        include_debug=include_debug,
                    )
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime_status":
                snapshot = realtime_store.snapshot()
                response_payload = {
                    "sequence": snapshot["sequence"],
                    "updated_at": snapshot["updated_at"],
                    "quote_count": len(snapshot["quotes"]),
                    "trade_event_count": sum(
                        len(events) for events in snapshot["trade_events"].values()
                    ),
                    "orderbook_event_count": sum(
                        len(events)
                        for events in snapshot["orderbook_events"].values()
                    ),
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/hot_selected":
                if str(query.get("clear", ["0"])[0]).strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }:
                    selected = set_selected_hot_codes([])
                elif any(
                    key in query
                    for key in ("selected", "selected_codes", "hot_codes", "codes", "code")
                ):
                    selected = set_selected_hot_codes(
                        _parse_hot_code_query(query)
                    )
                else:
                    selected = selected_hot_snapshot()
                response_payload = {
                    "lane": "hot",
                    "selected_codes": selected,
                    "hot_codes": current_hot_lane_codes(selected),
                    "candidate_codes": _hot_candidate_codes(rows),
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/hot_lane_status":
                selected = selected_hot_snapshot()
                with price_light_lock:
                    price_light_skip_count = price_light_consecutive_skips
                    last_hot_price_sequence = hot_lane_last_served_price_sequence
                hot_codes = current_hot_lane_codes()
                adaptive = evaluate_adaptive_speed(hot_codes)
                response_payload = {
                    "lane": "hot",
                    "candidate_limit": HOT_LANE_CANDIDATE_LIMIT,
                    "candidate_codes": _hot_candidate_codes(rows),
                    "selected_codes": selected,
                    "hot_codes": hot_codes,
                    "adaptive_speed": adaptive,
                    "slow_lane": "top100_latest_only",
                    "price_light_lane": {
                        "lane": "price_light",
                        "priority": "below_hot",
                        "top_limit": PRICE_LIGHT_TOP_LIMIT,
                        "fields": ["price", "change_rate"],
                        "max_consecutive_skips": (
                            PRICE_LIGHT_MAX_CONSECUTIVE_SKIPS
                        ),
                        "consecutive_skips": price_light_skip_count,
                        "hot_last_served_price_sequence": (
                            last_hot_price_sequence
                        ),
                        "adaptive_mode": adaptive["mode"],
                    },
                    "candidate_safe_ranking_lane": {
                        "lane": "candidate_safe_ranking",
                        "purpose": "preview_diagnostic",
                        "source": "store_price_light_snapshot",
                        "price_max_age_sec": CANDIDATE_SAFE_PRICE_MAX_AGE_SEC,
                        "orderbook_max_age_sec": (
                            CANDIDATE_SAFE_ORDERBOOK_MAX_AGE_SEC
                        ),
                        "backoff_ms": CANDIDATE_SAFE_BACKOFF_MS,
                        "commits_to_top100": False,
                        "adaptive_mode": adaptive["mode"],
                    },
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path in (
                "/api/candidate_safe_ranking",
                "/api/candidate_preview",
            ):
                query_selected = _parse_hot_code_query(query)
                hot_codes = current_hot_lane_codes(query_selected)
                stock_codes = list(dict.fromkeys(_price_light_codes(rows) + hot_codes))
                try:
                    price_light_snapshot = realtime_store.snapshot_price_light(
                        stock_codes
                    )
                except Exception as error:
                    price_light_snapshot = {
                        "sequence": None,
                        "price_sequence": None,
                        "updated_at": None,
                        "quotes": {},
                    }
                    gate = {
                        "allowed": False,
                        "skip_reason": "price_light_snapshot_error",
                        "retry_after_ms": max(1, CANDIDATE_SAFE_BACKOFF_MS),
                        "error": str(error),
                        "hot_diagnostics": [],
                        "adaptive_speed": evaluate_adaptive_speed(hot_codes),
                    }
                else:
                    gate = candidate_safe_gate(hot_codes, price_light_snapshot)
                response_payload = _candidate_safe_lane_payload(
                    rows,
                    price_light_snapshot,
                    hot_codes,
                    gate,
                )
                response_payload["selected_codes"] = list(
                    dict.fromkeys(selected_hot_snapshot() + query_selected)
                )
                response_payload["requested_codes"] = stock_codes
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/price_light_patch":
                since_price_sequence, fallback, fallback_reason = (
                    _parse_price_light_since(query)
                )
                requested_codes = _parse_codes_query(query)
                stock_codes = (
                    [
                        code
                        for code in (_stock_code(raw_code) for raw_code in requested_codes)
                        if code is not None
                    ]
                    if requested_codes
                    else _price_light_codes(rows)
                )
                stock_codes = list(dict.fromkeys(stock_codes))
                hot_codes = current_hot_lane_codes(_parse_hot_code_query(query))
                gate = price_light_gate(hot_codes)
                response_payload = _price_light_patch_payload(
                    realtime_store,
                    stock_codes,
                    since_price_sequence=since_price_sequence,
                    fallback=fallback,
                    fallback_reason=fallback_reason,
                    skipped=not gate["allowed"],
                    skip_reason=gate["skip_reason"],
                    retry_after_ms=gate["retry_after_ms"],
                )
                response_payload["hot_codes"] = hot_codes
                response_payload["hot_price_sequence"] = gate["hot_price_sequence"]
                response_payload["hot_last_served_price_sequence"] = gate[
                    "hot_last_served_price_sequence"
                ]
                response_payload["adaptive_speed"] = gate.get("adaptive_speed")
                if gate["allowed"]:
                    note_price_light_served()
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime_patch":
                since_sequence = None
                fallback = False
                fallback_reason = None
                if "since_sequence" in query:
                    try:
                        since_sequence = int(query["since_sequence"][0])
                        if since_sequence < 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        since_sequence = None
                        fallback = True
                        fallback_reason = "invalid_since_sequence"
                response_payload = _realtime_patch_payload(
                    realtime_store,
                    since_sequence=since_sequence,
                    fallback=fallback,
                    fallback_reason=fallback_reason,
                )
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/hot_realtime_patch":
                since_sequence = None
                fallback = False
                fallback_reason = None
                if "since_sequence" in query:
                    try:
                        since_sequence = int(query["since_sequence"][0])
                        if since_sequence < 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        since_sequence = None
                        fallback = True
                        fallback_reason = "invalid_since_sequence"
                query_selected = _parse_hot_code_query(query)
                hot_codes = current_hot_lane_codes(query_selected)
                started_at = time.monotonic()
                response_payload = _realtime_patch_payload(
                    realtime_store,
                    since_sequence=since_sequence,
                    fallback=fallback,
                    fallback_reason=fallback_reason,
                    stock_codes=hot_codes,
                    lane="hot",
                )
                record_hot_api_latency((time.monotonic() - started_at) * 1000)
                note_hot_lane_served(response_payload)
                response_payload["adaptive_speed"] = evaluate_adaptive_speed(
                    hot_codes
                )
                response_payload["hot_codes"] = hot_codes
                response_payload["selected_codes"] = list(
                    dict.fromkeys(selected_hot_snapshot() + query_selected)
                )
                response_payload["candidate_codes"] = _hot_candidate_codes(rows)
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime_provider_status":
                if realtime_provider is None:
                    response_payload = {
                        "available": False,
                        "running": False,
                        "registered_count": 0,
                        "last_error": realtime_provider_error,
                        "last_received_at": None,
                    }
                else:
                    try:
                        response_payload = realtime_provider.status()
                    except Exception as error:
                        response_payload = {
                            "available": False,
                            "running": False,
                            "registered_count": 0,
                            "last_error": str(error),
                            "last_received_at": None,
                        }
                if realtime_provider_error and not response_payload.get("last_error"):
                    response_payload["last_error"] = realtime_provider_error
                response_payload["start_requested"] = (
                    realtime_provider_start_requested
                )
                response_payload["start_succeeded"] = (
                    realtime_provider_start_succeeded
                )
                response_payload["register_requested"] = (
                    realtime_provider_register_requested
                )
                response_payload["register_succeeded"] = (
                    realtime_provider_register_succeeded
                )
                if response_payload.get("registered_count") is None:
                    response_payload["registered_count"] = (
                        realtime_provider_registered_count
                    )
                response_payload["register_error"] = (
                    realtime_provider_register_error
                )
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/close_metrics_request":
                codes = _parse_codes_query(query)
                priority = (
                    query.get("priority", ["background"])[0]
                    or "background"
                )
                force = str(query.get("force", ["0"])[0]).strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                if realtime_provider is None:
                    response_payload = {
                        "accepted": len(codes),
                        "queued": 0,
                        "priority": priority,
                        "available": False,
                        "error": "realtime provider unavailable",
                    }
                else:
                    try:
                        probe = str(query.get("probe", [""])[0]).strip().lower()
                        if probe == "strength" or priority == "strength_probe":
                            if len(codes) != 1:
                                response_payload = {
                                    "accepted": len(codes),
                                    "queued": 0,
                                    "priority": priority,
                                    "available": True,
                                    "error": (
                                        "strength probe requires exactly one code"
                                    ),
                                }
                            else:
                                response_payload = realtime_provider.enqueue_strength_probe(
                                    codes[0],
                                    priority=priority,
                                    force=force,
                                )
                                response_payload["accepted"] = 1
                                response_payload["queued"] = 0
                                response_payload["priority"] = "strength_probe"
                        elif probe == "orderbook" or priority == "orderbook_probe":
                            if len(codes) != 1:
                                response_payload = {
                                    "accepted": len(codes),
                                    "queued": 0,
                                    "priority": priority,
                                    "available": True,
                                    "error": (
                                        "orderbook probe requires exactly one code"
                                    ),
                                }
                            else:
                                response_payload = realtime_provider.enqueue_orderbook_probe(
                                    codes[0],
                                    priority=priority,
                                    force=force,
                                )
                                response_payload["accepted"] = 1
                                response_payload["queued"] = 0
                                response_payload["priority"] = "orderbook_probe"
                        else:
                            response_payload = realtime_provider.enqueue_close_metrics(
                                codes,
                                priority=priority,
                                force=force,
                            )
                        response_payload["available"] = True
                    except Exception as error:
                        response_payload = {
                            "accepted": len(codes),
                            "queued": 0,
                            "priority": priority,
                            "available": False,
                            "error": str(error),
                        }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/close_metrics_status":
                codes = _parse_codes_query(query)
                if realtime_provider is None:
                    response_payload = {
                        "available": False,
                        "queue_size": 0,
                        "snapshots": realtime_store.close_metrics_snapshot(codes),
                        "error": "realtime provider unavailable",
                    }
                else:
                    try:
                        response_payload = realtime_provider.close_metrics_status(
                            codes
                        )
                        response_payload["available"] = True
                    except Exception as error:
                        response_payload = {
                            "available": False,
                            "queue_size": 0,
                            "snapshots": realtime_store.close_metrics_snapshot(
                                codes
                            ),
                            "error": str(error),
                        }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/market_supply":
                market_supply_status = market_supply.get("_status", {})
                response_payload = {
                    "market_session": _market_session(),
                    "available": market_supply_status.get("available", True),
                    "status": market_supply_status.get("status", "available"),
                    "error": market_supply_status.get("error"),
                    "flow_date": market_supply_status.get("flow_date"),
                    "kospi": market_supply["kospi"],
                    "kosdaq": market_supply["kosdaq"],
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/top100_filter_report":
                dropped_count = sum(
                    1 for row in filter_report_rows if not row["filter_passed"]
                )
                response_payload = {
                    "source": "ka10032",
                    "request": {
                        "mrkt_tp": "000",
                        "mang_stk_incls": "0",
                        "stex_tp": "3",
                    },
                    "raw_count": len(filter_report_rows),
                    "displayed_count": len(filter_report_rows) - dropped_count,
                    "dropped_count": dropped_count,
                    "rows": filter_report_rows,
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/top100":
                include_debug = _query_flag_enabled(query, "debug", "include_debug")
                ohlc_count = sum(row.get("ohlc") is not None for row in rows)
                rows_id = id(rows)
                print(
                    f"/api/top100 response rows: {len(rows)}, "
                    f"ohlc count: {ohlc_count}, rows_id: {rows_id}, "
                    f"pid: {os.getpid()}",
                    flush=True,
                )
                if (
                    len(rows) != expected_row_count
                    or ohlc_count != expected_ohlc_count
                    or rows_id != expected_rows_id
                ):
                    detail = {
                        "error": "top100 response invariant failed",
                        "rows": len(rows),
                        "ohlc": ohlc_count,
                    }
                    body = json.dumps(detail).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                response_rows = enrich_candidate_fields(
                    _top100_with_realtime(
                        rows,
                        realtime_store,
                        include_debug=include_debug,
                    )
                )
                body = json.dumps(response_rows, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-StockBoard-PID", str(os.getpid()))
                self.send_header("X-StockBoard-OHLC-Count", str(ohlc_count))
                self.send_header("X-StockBoard-Rows-ID", str(rows_id))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    return RequestHandler


def main():
    _stop_existing_server(HOST, PORT)

    if not DOCS_DIR.is_dir():
        raise RuntimeError(f"docs directory not found: {DOCS_DIR}")

    access_token = issue_access_token()
    query_date = _query_date()
    market_supply = fetch_market_supply(access_token, query_date)
    top100_rows, page_counts = fetch_trade_value_top100(access_token)
    tradable_codes = _load_tradable_stock_codes()
    print(f"master count: {len(tradable_codes)}")
    print(f"master code samples: {sorted(tradable_codes)[:10]}")
    print(f"API normalized code samples: {[row['stock_code'] for row in top100_rows[:10]]}")
    program_net_enabled = _program_net_enabled()
    print(
        "ka90004 program net enabled: "
        f"{str(program_net_enabled).lower()}"
    )
    print(f"ka90004 query date: {query_date}")
    program_data = None
    program_net_by_code = {}
    if program_net_enabled:
        try:
            program_data = fetch_program_net(access_token, query_date)
            program_net_by_code = program_data["values"]
        except (RuntimeError, ValueError) as error:
            print(
                f"warning: ka90004 program net disabled after error: {error}",
                file=sys.stderr,
            )
    foreign_sum_data = None
    foreign_sum_by_code = {}
    try:
        foreign_sum_data = fetch_foreign_sum(access_token, query_date)
        foreign_sum_by_code = foreign_sum_data["values"]
    except (RuntimeError, ValueError) as error:
        print(
            f"warning: ka10037 foreign sum disabled after error: {error}",
            file=sys.stderr,
        )
    market_session = _market_session()
    foreign_investor_net_data = _empty_foreign_investor_net_data(query_date)
    if market_session == "장마감":
        try:
            foreign_investor_net_data = (
                fetch_foreign_investor_net_after_close(
                    access_token, query_date
                )
            )
        except (RuntimeError, ValueError) as error:
            foreign_investor_net_data = _empty_foreign_investor_net_data(
                query_date, error
            )
            print(
                f"warning: ka10066 foreign investor net disabled after error: "
                f"{error}",
                file=sys.stderr,
            )
    foreign_investor_net_by_code = foreign_investor_net_data["values"]
    filtered_rows = prepare_display_rows(
        top100_rows, tradable_codes, program_net_by_code
    )
    filter_report_rows = build_top100_filter_report(
        top100_rows, filtered_rows, tradable_codes
    )
    for row in filtered_rows:
        row["foreign_sum"] = foreign_sum_by_code.get(row["stock_code"])
        row["foreign_investor_net"] = foreign_investor_net_by_code.get(
            row["stock_code"]
        )
    _apply_foreign_display(filtered_rows, market_session)
    foreign_sum_joined_count = sum(
        row.get("foreign_sum") is not None for row in filtered_rows
    )
    if foreign_sum_data is not None:
        foreign_sum_data["stats"]["joined_count"] = foreign_sum_joined_count
    foreign_investor_net_joined_count = sum(
        row.get("foreign_investor_net") is not None for row in filtered_rows
    )
    foreign_investor_net_data["stats"]["joined_count"] = (
        foreign_investor_net_joined_count
    )
    program_net_joined_count = sum(
        row.get("program_net") is not None for row in filtered_rows
    )
    request_sleep_sec = _request_sleep_sec()
    ohlc_data = fetch_ohlc(
        access_token,
        filtered_rows,
        query_date,
        request_sleep_sec,
    )
    top100_count = len(filtered_rows)
    response_ohlc_count = sum(row.get("ohlc") is not None for row in filtered_rows)
    if (
        id(filtered_rows) != ohlc_data["rows_id"]
        or response_ohlc_count != ohlc_data["joined_count"]
        or response_ohlc_count != ohlc_data["attached_count"]
    ):
        raise RuntimeError(
            "ka10086 fetch_ohlc result does not match /api/top100 rows: "
            f"joined={ohlc_data['joined_count']}, "
            f"attached={ohlc_data['attached_count']}, "
            f"response={response_ohlc_count}, "
            f"fetch_rows_id={ohlc_data['rows_id']}, "
            f"response_rows_id={id(filtered_rows)}"
        )
    print(
        f"ka10086 OHLC count before /api/top100: {response_ohlc_count}, "
        f"rows: {len(filtered_rows)}, rows_id: {id(filtered_rows)}, "
        f"pid: {os.getpid()}",
        flush=True,
    )
    realtime_store = RealtimeStore()
    realtime_store.set_base_ohlc_many(filtered_rows)
    realtime_provider = None
    realtime_provider_error = None
    realtime_provider_start_requested = (
        os.getenv("STOCKBOARD_ENABLE_COM_REALTIME", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    realtime_provider_register_requested = (
        os.getenv("STOCKBOARD_REGISTER_TOP100_REALTIME", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    realtime_provider_start_succeeded = False
    realtime_provider_register_succeeded = False
    realtime_provider_registered_count = 0
    realtime_provider_register_error = None
    try:
        realtime_provider = KiwoomOpenApiRealtimeProvider(store=realtime_store)
        if hasattr(realtime_provider, "set_hot_priority_codes"):
            realtime_provider.set_hot_priority_codes(
                _hot_lane_codes(filtered_rows)
            )
    except Exception as error:
        realtime_provider_error = str(error)
        print(
            f"warning: realtime provider creation failed: {error}",
            file=sys.stderr,
        )
    if realtime_provider_start_requested and realtime_provider is not None:
        try:
            realtime_provider_start_succeeded = bool(realtime_provider.start())
            if not realtime_provider_start_succeeded:
                provider_status = realtime_provider.status()
                realtime_provider_error = (
                    provider_status.get("last_error")
                    or "realtime provider start returned false"
                )
        except Exception as error:
            realtime_provider_error = str(error)
            print(
                f"warning: realtime provider start failed: {error}",
                file=sys.stderr,
            )
    if realtime_provider_register_requested:
        if realtime_provider_start_succeeded and realtime_provider is not None:
            registration_codes = _realtime_stock_codes(filtered_rows)
            try:
                realtime_provider.register_codes(registration_codes)
                realtime_provider_register_succeeded = True
                provider_status = realtime_provider.status()
                realtime_provider_registered_count = provider_status.get(
                    "registered_count", len(registration_codes)
                )
            except Exception as error:
                realtime_provider_register_error = str(error)
                print(
                    f"warning: realtime provider registration failed: {error}",
                    file=sys.stderr,
                )
        else:
            realtime_provider_register_error = (
                "realtime provider start did not succeed"
            )
    if realtime_provider_start_succeeded and realtime_provider is not None:
        try:
            initial_close_metric_rows = enrich_candidate_fields(
                deepcopy(filtered_rows)
            )
            realtime_provider.enqueue_close_metrics(
                _close_metric_codes(initial_close_metric_rows),
                priority="initial",
            )
        except Exception as error:
            print(
                f"warning: close metrics initial enqueue failed: {error}",
                file=sys.stderr,
            )
    server = ThreadingHTTPServer(
        (HOST, PORT),
        make_handler(
            filtered_rows,
            filter_report_rows,
            expected_row_count=len(filtered_rows),
            expected_ohlc_count=response_ohlc_count,
            expected_rows_id=id(filtered_rows),
            market_supply=market_supply,
            realtime_store=realtime_store,
            realtime_provider=realtime_provider,
            realtime_provider_error=realtime_provider_error,
            realtime_provider_start_requested=realtime_provider_start_requested,
            realtime_provider_start_succeeded=realtime_provider_start_succeeded,
            realtime_provider_register_requested=(
                realtime_provider_register_requested
            ),
            realtime_provider_register_succeeded=(
                realtime_provider_register_succeeded
            ),
            realtime_provider_registered_count=realtime_provider_registered_count,
            realtime_provider_register_error=realtime_provider_register_error,
        ),
    )
    print(f"server pid={os.getpid()}", flush=True)
    print(f"server url=http://{HOST}:{PORT}/", flush=True)
    for page_number, page_count in enumerate(page_counts, start=1):
        print(f"page{page_number} count: {page_count}")
    print(f"unique count: {len(top100_rows)}")
    print(f"filtered count: {len(filtered_rows)}")
    print(f"top100 count: {top100_count}")
    print(f"ka20001 market supply: {market_supply}")
    market_counts = (
        program_data["market_counts"]
        if program_data is not None
        else {"KOSPI": 0, "KOSDAQ": 0}
    )
    program_net_divisor = (
        program_data["divisor"]
        if program_data is not None
        else os.getenv("KIWOOM_PROGRAM_NET_EOK_DIVISOR", "100")
    )
    print(f"ka90004 market KOSPI count: {market_counts['KOSPI']}")
    print(f"ka90004 market KOSDAQ count: {market_counts['KOSDAQ']}")
    print(f"ka90004 collected count: {len(program_net_by_code)}")
    print(f"ka90004 joined count: {program_net_joined_count}")
    print(
        "ka90004 raw samples: "
        f"{program_data['raw_samples'] if program_data is not None else []}"
    )
    print(
        "ka90004 converted samples: "
        f"{program_data['converted_samples'] if program_data is not None else []}"
    )
    print(f"ka90004 divisor: {program_net_divisor}")
    print(
        "ka90004 request sleep sec: "
        f"{program_data['request_sleep_seconds'] if program_data is not None else None}"
    )
    print(
        "ka90004 errors: "
        f"{program_data['errors'] if program_data is not None else []}"
    )
    print(
        "ka90004 HTTP 429 position: "
        f"{program_data['rate_limit'] if program_data is not None else None}"
    )
    foreign_sum_stats = (
        foreign_sum_data["stats"]
        if foreign_sum_data is not None
        else {
            "enabled": os.getenv("KIWOOM_FOREIGN_SUM_ENABLED", "1").strip() == "1",
            "query_date": query_date,
            "market_counts": {"KOSPI": 0, "KOSDAQ": 0},
            "joined_count": foreign_sum_joined_count,
            "raw_samples": [],
            "converted_samples": [],
            "rate_limit": None,
        }
    )
    print(
        "ka10037 enabled: "
        f"{str(foreign_sum_stats['enabled']).lower()}"
    )
    print(f"ka10037 query date: {foreign_sum_stats['query_date']}")
    print(
        "ka10037 market KOSPI count: "
        f"{foreign_sum_stats['market_counts']['KOSPI']}"
    )
    print(
        "ka10037 market KOSDAQ count: "
        f"{foreign_sum_stats['market_counts']['KOSDAQ']}"
    )
    print(f"ka10037 joined count: {foreign_sum_stats['joined_count']}")
    print(f"ka10037 raw samples: {foreign_sum_stats['raw_samples']}")
    print(
        "ka10037 converted samples: "
        f"{foreign_sum_stats['converted_samples']}"
    )
    print(
        "ka10037 HTTP 429 position: "
        f"{foreign_sum_stats['rate_limit']}"
    )
    print(
        "foreign investor net after close available: "
        f"{str(foreign_investor_net_data['stats']['available']).lower()}"
    )
    print(
        "ka10066 market KOSPI count: "
        f"{foreign_investor_net_data['stats']['market_counts']['KOSPI']}"
    )
    print(
        "ka10066 market KOSDAQ count: "
        f"{foreign_investor_net_data['stats']['market_counts']['KOSDAQ']}"
    )
    print(
        "ka10066 collected count: "
        f"{foreign_investor_net_data['stats']['collected_count']}"
    )
    print(
        "foreign investor net after close joined count: "
        f"{foreign_investor_net_joined_count}"
    )
    print(
        "ka10066 raw samples: "
        f"{foreign_investor_net_data['stats']['raw_samples']}"
    )
    print(
        "ka10066 converted samples: "
        f"{foreign_investor_net_data['stats']['converted_samples']}"
    )
    print(
        "ka10066 request sleep sec: "
        f"{foreign_investor_net_data['stats']['request_sleep_seconds']}"
    )
    print(
        "ka10066 errors: "
        f"{foreign_investor_net_data['stats']['errors']}"
    )
    print(
        "ka10066 HTTP 429 position: "
        f"{foreign_investor_net_data['stats']['rate_limit']}"
    )
    print(f"ka10086 query date: {query_date}")
    print("ka10086 OHLC limit: all")
    print(f"ka10086 request sleep sec: {request_sleep_sec}")
    print(f"ka10086 target count: {ohlc_data['target_count']}")
    print(f"ka10086 OHLC joined count: {ohlc_data['joined_count']}")
    print(f"ka10086 OHLC failed count: {ohlc_data['failed_count']}")
    print(f"ka10086 OHLC first failed samples: {ohlc_data['failed_samples']}")
    print(f"ka10086 raw samples: {ohlc_data['raw_samples']}")
    print(f"ka10086 converted samples: {ohlc_data['converted_samples']}")
    print(f"ka10086 VWAP samples: {ohlc_data['vwap_samples']}")
    print(f"ka10086 HTTP 429 position: {ohlc_data['rate_limit']}")
    print(f"Open http://{HOST}:{PORT}/stockboard_v0_3_0_sample.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if realtime_provider_start_succeeded and realtime_provider is not None:
            try:
                realtime_provider.stop()
            except Exception as error:
                print(
                    f"warning: realtime provider stop failed: {error}",
                    file=sys.stderr,
                )
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
