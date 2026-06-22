import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


KST = timezone(timedelta(hours=9))
# ka90004 netprps_prica is provisionally treated as KRW millions: 100 million KRW per eok.
PROGRAM_NET_EOK_DIVISOR_TEXT = os.getenv("KIWOOM_PROGRAM_NET_EOK_DIVISOR", "100")
REQUEST_SLEEP_SEC_TEXT = os.getenv("KIWOOM_REQUEST_SLEEP_SEC", "0.25")

OUTPUT_KEYS = (
    "stock_code", "rank", "original_rank", "prev_rank", "grade", "stock_name",
    "price", "change_rate", "trade_value_eok", "ohlc", "bid_ask_ratio",
    "strength_1m", "strength_day", "foreign_sum", "foreign_investor_net",
    "foreign_display_label", "foreign_display_value", "foreign_display_source",
    "program_net", "big_hand", "momentum",
)


def prepare_display_rows(top100_rows, tradable_codes, program_net_by_code):
    filtered_rows = [row for row in top100_rows if row["stock_code"] in tradable_codes]
    for display_rank, row in enumerate(filtered_rows, start=1):
        row["rank"] = display_rank
        row["program_net"] = program_net_by_code.get(row["stock_code"])
    return filtered_rows


def _first(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _clean_number(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return value
    return int(number) if number == number.to_integral() else float(number)


def _stock_code(value):
    if value in (None, ""):
        return None
    code = "".join(str(value).split()).upper()
    code = code.split("_", 1)[0]
    if code.startswith("A"):
        code = code[1:]
    if not code.isdigit():
        return None
    code = code.zfill(6)
    return code if len(code) == 6 else None


def _absolute_number(value):
    number = _clean_number(value)
    return abs(number) if isinstance(number, (int, float)) else number


def _trade_value_eok(value):
    """ka10032 거래대금(백만원)을 억원 단위로 변환한다."""
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    eok = Decimal(str(number)) / Decimal("100")
    return int(eok) if eok == eok.to_integral() else float(eok)


def _program_net_eok_divisor():
    try:
        divisor = Decimal(PROGRAM_NET_EOK_DIVISOR_TEXT)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_PROGRAM_NET_EOK_DIVISOR must be a positive number"
        ) from error
    if not divisor.is_finite() or divisor <= 0:
        raise RuntimeError("KIWOOM_PROGRAM_NET_EOK_DIVISOR must be a positive number")
    return divisor


def _program_net_eok(value, divisor):
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    eok = Decimal(str(number)) / divisor
    return int(eok) if eok == eok.to_integral() else float(eok)


def _query_date():
    query_date = os.getenv("KIWOOM_QUERY_DATE")
    if query_date:
        query_date = query_date.strip()
    else:
        query_date = datetime.now(KST).strftime("%Y%m%d")
    if len(query_date) != 8 or not query_date.isdigit():
        raise RuntimeError("KIWOOM_QUERY_DATE must use YYYYMMDD format")
    try:
        datetime.strptime(query_date, "%Y%m%d")
    except ValueError as error:
        raise RuntimeError("KIWOOM_QUERY_DATE must be a valid YYYYMMDD date") from error
    return query_date


def _program_net_enabled():
    return os.getenv("KIWOOM_PROGRAM_NET_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _request_sleep_sec():
    try:
        seconds = Decimal(REQUEST_SLEEP_SEC_TEXT)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_REQUEST_SLEEP_SEC must be a non-negative number"
        ) from error
    if not seconds.is_finite() or seconds < 0:
        raise RuntimeError("KIWOOM_REQUEST_SLEEP_SEC must be a non-negative number")
    return float(seconds)


def _normalize_row(row):
    normalized = dict.fromkeys(OUTPUT_KEYS)
    normalized.update(
        {
            "stock_code": _stock_code(_first(row, "stk_cd", "stock_code", "종목코드")),
            "rank": _clean_number(_first(row, "now_rank", "rank", "현재순위")),
            "prev_rank": _clean_number(_first(row, "pred_rank", "prev_rank", "전일순위")),
            "stock_name": _first(row, "stk_nm", "stock_name", "종목명"),
            "price": _absolute_number(_first(row, "cur_prc", "price", "현재가")),
            "change_rate": _clean_number(_first(row, "flu_rt", "change_rate", "등락률")),
            "trade_value_eok": _trade_value_eok(
                _first(row, "trde_prica", "trade_value", "거래대금")
            ),
            "foreign_investor_net": _clean_number(
                _first(row, "foreign_investor_net")
            ),
            "foreign_display_label": _first(row, "foreign_display_label"),
            "foreign_display_value": _clean_number(
                _first(row, "foreign_display_value")
            ),
            "foreign_display_source": _first(row, "foreign_display_source"),
        }
    )
    normalized["original_rank"] = normalized["rank"]
    return normalized


def _required_market_number(response, field, market_name):
    value = _clean_number(response.get(field))
    if not isinstance(value, (int, float)):
        raise RuntimeError(
            f"ka20001 {market_name} missing numeric field {field}: {response.get(field)!r}"
        )
    return value


def _required_market_count(response, field, market_name):
    value = _required_market_number(response, field, market_name)
    if value < 0 or int(value) != value:
        raise RuntimeError(
            f"ka20001 {market_name} invalid count field {field}: {value!r}"
        )
    return int(value)


def _market_flow_number(value, field, market_name):
    text = str(value).strip().replace(",", "")
    if text.startswith("--"):
        text = "-" + text[2:]
    number = _clean_number(text)
    if not isinstance(number, (int, float)):
        raise RuntimeError(
            f"{market_name} missing numeric market flow field {field}: {value!r}"
        )
    return number


def _million_to_eok(value, field, market_name):
    number = _market_flow_number(value, field, market_name)
    eok = Decimal(str(number)) / Decimal("100")
    return int(eok) if eok == eok.to_integral() else float(eok)


def _recent_dates(query_date, days=7):
    date = datetime.strptime(query_date, "%Y%m%d")
    return [
        (date - timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(days)
    ]


def _ohlc_price(value):
    number = _clean_number(value)
    return abs(number) if isinstance(number, (int, float)) else None


def _daily_row_date(row):
    value = _first(row, "date", "dt", "일자")
    if value in (None, ""):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits if len(digits) == 8 else None


def _select_daily_rows(rows, query_date):
    eligible_rows = sorted(
        (
            (row_date, row)
            for row in rows
            if isinstance(row, dict)
            and (row_date := _daily_row_date(row))
            and row_date <= query_date
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not eligible_rows:
        return None, None

    current_date, current_row = eligible_rows[0]
    previous_row = next(
        (
            row
            for row_date, row in eligible_rows[1:]
            if row_date < current_date
        ),
        None,
    )
    return current_row, previous_row


def _build_ohlc(current_row, previous_row):
    if not current_row or not previous_row:
        return None, None

    prices = {
        "open": _ohlc_price(_first(current_row, "open_pric", "open")),
        "high": _ohlc_price(_first(current_row, "high_pric", "high")),
        "low": _ohlc_price(_first(current_row, "low_pric", "low")),
        "close": _ohlc_price(_first(current_row, "close_pric", "close")),
        "prev_high": _ohlc_price(_first(previous_row, "high_pric", "high")),
        "prev_close": _ohlc_price(_first(previous_row, "close_pric", "close")),
        "prev_low": _ohlc_price(_first(previous_row, "low_pric", "low")),
    }
    if any(value is None for value in prices.values()):
        return None, None
    if prices["high"] < prices["low"] or prices["prev_high"] < prices["prev_low"]:
        return None, None

    amount_million = _clean_number(_first(current_row, "amt_mn", "trade_value_mn"))
    trade_quantity = _clean_number(_first(current_row, "trde_qty", "trade_quantity"))
    vwap_candidate = None
    if (
        isinstance(amount_million, (int, float))
        and isinstance(trade_quantity, (int, float))
        and trade_quantity > 0
    ):
        vwap_decimal = (
            Decimal(str(amount_million)) * Decimal("1000000")
        ) / Decimal(str(trade_quantity))
        vwap_candidate = float(vwap_decimal)

    vwap = (
        round(vwap_candidate, 2)
        if vwap_candidate is not None
        and prices["low"] <= vwap_candidate <= prices["high"]
        else None
    )
    ohlc = {
        "open": prices["open"],
        "high": prices["high"],
        "low": prices["low"],
        "close": prices["close"],
        "vwap": vwap,
        "prev_high": prices["prev_high"],
        "prev_close": prices["prev_close"],
        "prev_low": prices["prev_low"],
        "trading_date": _daily_row_date(current_row),
    }
    return ohlc, {
        "amt_mn": amount_million,
        "trde_qty": trade_quantity,
        "calculated_vwap": round(vwap_candidate, 2)
        if vwap_candidate is not None
        else None,
        "accepted_vwap": vwap,
    }


def _ohlc_row_sample(row):
    if not row:
        return None
    return {
        "date": _first(row, "date", "dt", "일자"),
        "open_pric": _first(row, "open_pric", "open"),
        "high_pric": _first(row, "high_pric", "high"),
        "low_pric": _first(row, "low_pric", "low"),
        "close_pric": _first(row, "close_pric", "close"),
        "amt_mn": _first(row, "amt_mn", "trade_value_mn"),
        "trde_qty": _first(row, "trde_qty", "trade_quantity"),
    }
