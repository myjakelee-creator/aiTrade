from __future__ import annotations

"""Cross-PC, date-explicit StockBoard context writer.

This entrypoint extends the existing low-priority ka10086 bootstrap so every PC
can rebuild the same completed-session board without copying another PC's
``data/runtime`` directory. Historical rows use historical close fields only;
query-time ``cur_prc`` and ``flu_rt`` are diagnostics and are never selected as
the completed-day price or change rate.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from kiwoom_data_provider import _first, _post_json, issue_access_token
from realtime_v2 import context_snapshot_writer_singleflight as sf
from realtime_v2.common import normalize_code, now_text, to_number
from stockboard_previous_trade_value import previous_trade_value_from_daily_row

PORTABLE_POLICY_VERSION = "portable_closed_board_snapshot_v2"
PORTABLE_PARSER_VERSION = "exact_daily_row_fields_v1"
PORTABLE_MIN_COVERAGE = float(
    os.getenv("STOCKBOARD_PORTABLE_BOARD_MIN_COVERAGE", "0.70")
)
DIAGNOSTIC_SAMPLE_LIMIT = 20
ACTIVE_PHASES = {
    "premarket",
    "opening_call",
    "regular",
    "closing_call",
    "after_wait",
    "aftermarket",
}

_last_bootstrap_args: tuple[Path, int, float] | None = None
_attempted_refresh_keys: set[tuple[str, str]] = set()
_original_active_fetch_ohlc_bootstrap = sf.fetch_ohlc_bootstrap


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _row_date(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return _date_digits(
        _first(row, "date", "dt", "일자", "trd_dd", "bas_dt", "stck_bsop_date")
    )


def _normalized_number(value: Any, *, absolute: bool = False) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    result = float(number)
    return abs(result) if absolute else result


def _historical_close(row: dict[str, Any] | None) -> float | None:
    """Read a completed-day close without query-time current-price aliases."""
    if not isinstance(row, dict):
        return None
    return _normalized_number(
        _first(row, "close_pric", "close", "종가", "stck_clpr"),
        absolute=True,
    )


def _historical_change_rate(
    row: dict[str, Any] | None,
    previous_row: dict[str, Any] | None,
) -> float | None:
    """Compute the completed-day rate from two historical closes."""
    current = _historical_close(row)
    previous = _historical_close(previous_row)
    if current is None or previous is None or previous <= 0:
        return None
    return round((current / previous - 1.0) * 100.0, 4)


def _historical_ohlc(row: dict[str, Any] | None) -> dict[str, float] | None:
    if not isinstance(row, dict):
        return None
    open_price = _normalized_number(
        _first(row, "open_pric", "open", "시가", "stck_oprc"),
        absolute=True,
    )
    high_price = _normalized_number(
        _first(row, "high_pric", "high", "고가", "stck_hgpr"),
        absolute=True,
    )
    low_price = _normalized_number(
        _first(row, "low_pric", "low", "저가", "stck_lwpr"),
        absolute=True,
    )
    close_price = _historical_close(row)
    if any(
        value is None or value <= 0
        for value in (open_price, high_price, low_price, close_price)
    ):
        return None
    assert open_price is not None
    assert high_price is not None
    assert low_price is not None
    assert close_price is not None
    if low_price > high_price:
        return None
    if not (low_price <= open_price <= high_price):
        return None
    if not (low_price <= close_price <= high_price):
        return None
    return {
        "open": round(open_price, 4),
        "high": round(high_price, 4),
        "low": round(low_price, 4),
        "close": round(close_price, 4),
    }


def _trade_value_eok(row: dict[str, Any] | None) -> float | None:
    if not isinstance(row, dict):
        return None
    parsed = previous_trade_value_from_daily_row(row)
    value = to_number(parsed.get("prev_trade_value_eok"))
    return round(float(value), 4) if value is not None and value >= 0 else None


def _select_exact_and_previous(
    daily_rows: list[Any],
    target_date: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    dated = [
        (_row_date(row), row)
        for row in daily_rows
        if isinstance(row, dict) and _row_date(row)
    ]
    exact = next((row for date_text, row in dated if date_text == target_date), None)
    older = sorted(
        [(date_text, row) for date_text, row in dated if date_text < target_date],
        key=lambda item: item[0],
        reverse=True,
    )
    previous = older[0][1] if older else None
    return exact, previous


def _rows_are_historical_exact(
    rows: list[Any],
    target_date: str,
) -> tuple[bool, str]:
    exact, previous = _select_exact_and_previous(rows, target_date)
    if exact is None:
        return False, f"target date {target_date} missing"
    if previous is None:
        return False, f"previous completed row before {target_date} missing"
    if _historical_ohlc(exact) is None:
        return False, "historical OHLC fields invalid"
    if _historical_close(previous) is None:
        return False, "previous historical close missing"
    if _historical_change_rate(exact, previous) is None:
        return False, "historical change rate unavailable"
    if _trade_value_eok(exact) is None:
        return False, "historical trade value unavailable"
    return True, ""


def _request_daily_rows(
    token: str,
    code: str,
    target_date: str,
    sleep_sec: float,
) -> tuple[list[Any] | None, str | None, str | None]:
    last_error: str | None = None
    for registered_code, source_name in (
        (f"{code}_AL", "ka10086_AL_exact_date"),
        (code, "ka10086_regular_exact_date"),
    ):
        try:
            response = _post_json(
                "/api/dostk/mrkcond",
                {"stk_cd": registered_code, "qry_dt": target_date, "indc_tp": "1"},
                {
                    "Authorization": f"Bearer {token}",
                    "api-id": "ka10086",
                    "cont-yn": "N",
                    "next-key": "",
                },
            )
            rows = _first(response, "daly_stkpc", "daily_stock_price", "output")
            if isinstance(rows, list):
                usable, reason = _rows_are_historical_exact(rows, target_date)
                if usable:
                    return rows, source_name, None
                last_error = f"{registered_code}: {reason}"
            else:
                last_error = f"daily rows missing for {registered_code}"
        except Exception as error:
            last_error = f"{registered_code}: {type(error).__name__}: {error}"
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return None, None, last_error


def _query_code_for_source(code: str, source_name: str) -> str:
    return f"{code}_AL" if source_name.startswith("ka10086_AL") else code


def _diagnostic_sample(
    *,
    code: str,
    source_name: str,
    exact: dict[str, Any],
    selected_close: float,
    computed_change_rate: float,
    market_scope: str,
) -> dict[str, Any]:
    return {
        "stock_code": code,
        "row_date": _row_date(exact),
        "raw_cur_prc": _first(exact, "cur_prc", "현재가"),
        "raw_close_pric": _first(exact, "close_pric", "close", "종가", "stck_clpr"),
        "raw_flu_rt": _first(exact, "flu_rt", "change_rate", "등락률", "stck_prdy_ctrt"),
        "selected_close": selected_close,
        "computed_change_rate": computed_change_rate,
        "query_code": _query_code_for_source(code, source_name),
        "source": source_name,
        "market_scope": market_scope,
    }


def build_portable_snapshot(
    codes_file: Path,
    limit: int,
    sleep_sec: float,
    target_date: str,
    market_phase: str,
) -> dict[str, Any]:
    path = Path(codes_file)
    codes: list[str] = []
    if path.is_file():
        codes = [
            normalize_code(line.strip())
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if normalize_code(line.strip())
        ]
    codes = codes[: max(1, int(limit or 300))]
    token = issue_access_token()

    ohlc_values: dict[str, dict[str, Any]] = {}
    board_values: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    diagnostics: list[dict[str, Any]] = []

    for code in codes:
        rows, source_name, error_text = _request_daily_rows(
            token,
            code,
            target_date,
            max(0.0, float(sleep_sec or 0.0)),
        )
        if not isinstance(rows, list) or not source_name:
            if len(errors) < 30:
                errors.append(
                    {"stock_code": code, "error": error_text or "no exact historical row"}
                )
            continue

        exact, previous = _select_exact_and_previous(rows, target_date)
        ohlc_core = _historical_ohlc(exact)
        price = _historical_close(exact)
        change_rate = _historical_change_rate(exact, previous)
        trade_value = _trade_value_eok(exact)
        previous_value = _trade_value_eok(previous)
        previous_date = _row_date(previous)

        if (
            exact is None
            or previous is None
            or ohlc_core is None
            or price is None
            or change_rate is None
            or trade_value is None
        ):
            if len(errors) < 30:
                errors.append(
                    {"stock_code": code, "error": "historical exact core field missing"}
                )
            continue

        market_scope = (
            "integrated_AL" if source_name.startswith("ka10086_AL") else "regular_KRX"
        )
        ohlc = {
            **ohlc_core,
            "source": source_name,
            "date": target_date,
            "source_trading_date": target_date,
            "market_scope": market_scope,
            "portable_parser_version": PORTABLE_PARSER_VERSION,
        }
        ohlc_values[code] = ohlc
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

        board_values[code] = {
            "stock_code": code,
            "price": price,
            "change_rate": change_rate,
            "trade_value_eok": trade_value,
            "prev_trade_value_eok": previous_value,
            "prev_trade_value_date": previous_date or None,
            "ohlc": ohlc,
            "source_trading_date": target_date,
            "price_trading_date": target_date,
            "change_rate_trading_date": target_date,
            "trade_value_trading_date": target_date,
            "ohlc_trading_date": target_date,
            "market_scope": market_scope,
            "source": source_name,
            "quality": "EXACT_HISTORICAL_FIELDS",
            "portable_parser_version": PORTABLE_PARSER_VERSION,
        }
        if len(diagnostics) < DIAGNOSTIC_SAMPLE_LIMIT:
            diagnostics.append(
                _diagnostic_sample(
                    code=code,
                    source_name=source_name,
                    exact=exact,
                    selected_close=price,
                    computed_change_rate=change_rate,
                    market_scope=market_scope,
                )
            )

    coverage = len(board_values) / len(codes) if codes else 0.0
    verified = bool(codes) and coverage >= max(
        0.1, min(1.0, PORTABLE_MIN_COVERAGE)
    )
    return {
        "schema_version": 3,
        "source": "stockboard_v2_portable_ka10086_exact_historical_fields",
        "portable_policy_version": PORTABLE_POLICY_VERSION,
        "portable_parser_version": PORTABLE_PARSER_VERSION,
        "ts": now_text(),
        "trading_date": target_date,
        "source_trading_date": target_date,
        "market_phase": market_phase,
        "market_scope": "integrated_AL_regular_fallback",
        "count": len(ohlc_values),
        "board_value_count": len(board_values),
        "requested_count": len(codes),
        "coverage": round(coverage, 4),
        "verified": verified,
        "minimum_coverage": PORTABLE_MIN_COVERAGE,
        "source_counts": source_counts,
        "error_count": len(errors),
        "errors": errors,
        "diagnostic_samples": diagnostics,
        "values": ohlc_values,
        "board_values": board_values,
    }


def _candidate_path() -> Path:
    active = Path(sf.base.OHLC_SNAPSHOT_FILE)
    return active.with_name("ohlc_snapshot_candidate.json")


def validate_portable_candidate(
    payload: Any,
    target_date: str,
) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "candidate is not a dict"
    if payload.get("portable_policy_version") != PORTABLE_POLICY_VERSION:
        return False, "portable policy version mismatch"
    if payload.get("portable_parser_version") != PORTABLE_PARSER_VERSION:
        return False, "portable parser version mismatch"
    source_date = _date_digits(
        payload.get("source_trading_date") or payload.get("trading_date")
    )
    if not source_date or source_date != _date_digits(target_date):
        return False, "candidate trading date mismatch"
    if payload.get("verified") is not True:
        return False, "candidate coverage is not verified"

    requested = int(to_number(payload.get("requested_count")) or 0)
    board_values = payload.get("board_values")
    if requested <= 0 or not isinstance(board_values, dict) or not board_values:
        return False, "candidate board values missing"
    coverage = len(board_values) / requested
    if coverage + 1e-9 < max(0.1, min(1.0, PORTABLE_MIN_COVERAGE)):
        return False, "candidate coverage below minimum"

    for raw_code, raw_value in board_values.items():
        code = normalize_code(raw_code)
        if not code or not isinstance(raw_value, dict):
            return False, "candidate contains invalid code/value"
        if raw_value.get("portable_parser_version") != PORTABLE_PARSER_VERSION:
            return False, f"{code}: row parser version mismatch"
        for date_key in (
            "source_trading_date",
            "price_trading_date",
            "change_rate_trading_date",
            "trade_value_trading_date",
            "ohlc_trading_date",
        ):
            if _date_digits(raw_value.get(date_key)) != source_date:
                return False, f"{code}: {date_key} mismatch"
        price = _historical_close({"close_pric": raw_value.get("price")})
        rate = to_number(raw_value.get("change_rate"))
        trade_value = to_number(raw_value.get("trade_value_eok"))
        ohlc = raw_value.get("ohlc")
        if (
            price is None
            or rate is None
            or trade_value is None
            or not isinstance(ohlc, dict)
        ):
            return False, f"{code}: core field missing"
        parsed_ohlc = _historical_ohlc(
            {
                "open_pric": ohlc.get("open"),
                "high_pric": ohlc.get("high"),
                "low_pric": ohlc.get("low"),
                "close_pric": ohlc.get("close"),
            }
        )
        if parsed_ohlc is None:
            return False, f"{code}: OHLC range invalid"
        if abs(float(parsed_ohlc["close"]) - float(price)) > 0.0001:
            return False, f"{code}: price/OHLC close mismatch"
        if _date_digits(ohlc.get("date") or ohlc.get("source_trading_date")) != source_date:
            return False, f"{code}: OHLC date mismatch"
    return True, "ok"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _snapshot_is_ready(target_date: str) -> bool:
    payload = _read_json(Path(sf.base.OHLC_SNAPSHOT_FILE))
    valid, _reason = validate_portable_candidate(payload, target_date)
    return valid


def fetch_ohlc_bootstrap(
    codes_file: Path,
    limit: int = 300,
    sleep_sec: float = 0.12,
):
    global _last_bootstrap_args
    target_date, phase = sf.market_supply_target_context()
    path = Path(codes_file)
    _last_bootstrap_args = (path, int(limit or 300), float(sleep_sec or 0.0))

    # Keep the proven intraday OHLC bootstrap unchanged. Portable historical
    # reconstruction is only for closed/weekend/holiday display.
    if phase in ACTIVE_PHASES:
        return _original_active_fetch_ohlc_bootstrap(path, limit, sleep_sec)

    payload = sf.coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka10086_portable_board_bundle_v2",
        params={
            "codes_fingerprint": sf._file_fingerprint(path),
            "limit": int(limit or 300),
            "target_trading_date": target_date,
            "suffix_policy": "AL_historical_fields_then_regular_fallback",
            "portable_policy_version": PORTABLE_POLICY_VERSION,
            "portable_parser_version": PORTABLE_PARSER_VERSION,
        },
        trading_date=target_date,
        market_session=phase or "unknown",
        ttl_sec=6 * 60 * 60,
        wait_timeout_sec=max(
            120.0,
            float(limit or 300) * max(0.05, float(sleep_sec)) * 3.0,
        ),
        lease_timeout_sec=30 * 60,
        fetcher=lambda: build_portable_snapshot(
            path,
            limit,
            sleep_sec,
            target_date,
            phase,
        ),
    )
    payload = dict(payload) if isinstance(payload, dict) else {}
    sf._atomic_write(_candidate_path(), payload)
    valid, reason = validate_portable_candidate(payload, target_date)
    if not valid:
        raise RuntimeError(f"portable candidate rejected: {reason}")
    _attempted_refresh_keys.add((target_date, phase))
    return payload


_original_inject_context_status = sf._inject_context_status
_original_run_cycle = sf._run_cycle


def _inject_context_status(payload):
    status = _original_inject_context_status(payload)
    status["portable_board_policy_version"] = PORTABLE_POLICY_VERSION
    status["portable_board_parser_version"] = PORTABLE_PARSER_VERSION
    status["portable_board_candidate_path"] = str(_candidate_path())
    status["context_entrypoint"] = "realtime_v2.context_snapshot_writer_portable"
    return status


def _run_cycle(status: dict) -> None:
    _original_run_cycle(status)
    target_date, phase = sf.market_supply_target_context()
    status["portable_board_target_trading_date"] = target_date
    status["portable_board_market_phase"] = phase
    status["portable_board_parser_version"] = PORTABLE_PARSER_VERSION
    status["portable_board_candidate_path"] = str(_candidate_path())

    if phase in ACTIVE_PHASES or _snapshot_is_ready(target_date):
        status["portable_board_refresh_status"] = (
            "active_session_initial_only" if phase in ACTIVE_PHASES else "exact_ready"
        )
        sf.write_status(status)
        return

    refresh_key = (target_date, phase)
    if refresh_key in _attempted_refresh_keys:
        status["portable_board_refresh_status"] = "attempted_once_not_ready"
        sf.write_status(status)
        return

    if _last_bootstrap_args is None:
        status["portable_board_refresh_status"] = "bootstrap_arguments_unavailable"
        sf.write_status(status)
        return

    _attempted_refresh_keys.add(refresh_key)
    path, limit, sleep_sec = _last_bootstrap_args
    try:
        payload = fetch_ohlc_bootstrap(path, limit, sleep_sec)
        # Promotion is atomic and happens only after candidate validation succeeds.
        sf._atomic_write(sf.base.OHLC_SNAPSHOT_FILE, payload)
        status["portable_board_refresh_status"] = (
            "exact_ready" if _snapshot_is_ready(target_date) else "promotion_failed"
        )
        status["portable_board_refresh_target_trading_date"] = target_date
        status["portable_board_refresh_count"] = payload.get("board_value_count")
        status["portable_board_refresh_coverage"] = payload.get("coverage")
        status.pop("portable_board_refresh_error", None)
    except Exception as error:
        status["portable_board_refresh_status"] = "failed_once_active_unchanged"
        status["portable_board_refresh_error"] = str(error)
    sf.write_status(status)


sf.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
sf.base.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
sf._inject_context_status = _inject_context_status
sf._run_cycle = _run_cycle


if __name__ == "__main__":
    raise SystemExit(sf.main())
