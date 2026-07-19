from __future__ import annotations

"""Cross-PC, date-explicit StockBoard context writer.

This entrypoint replaces the existing ka10086 OHLC bootstrap with one equivalent
low-priority pass that also extracts exact target-day board fields.  Every PC can
therefore rebuild the same closed-session baseline from Kiwoom REST without
copying another PC's ``data/runtime`` directory.
"""

import os
import time
from pathlib import Path
from typing import Any

from kiwoom_data_provider import _first, _post_json, issue_access_token
from realtime_v2 import context_snapshot_writer_singleflight as sf
from realtime_v2.common import normalize_code, now_text, to_number
from stockboard_previous_trade_value import previous_trade_value_from_daily_row

PORTABLE_POLICY_VERSION = "portable_closed_board_snapshot_v1"
PORTABLE_MIN_COVERAGE = float(os.getenv("STOCKBOARD_PORTABLE_BOARD_MIN_COVERAGE", "0.70"))


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


def _close_price(row: dict[str, Any] | None) -> float | None:
    if not isinstance(row, dict):
        return None
    return _normalized_number(
        _first(row, "cur_prc", "close_pric", "close", "현재가", "종가", "stck_clpr"),
        absolute=True,
    )


def _change_rate(row: dict[str, Any], previous_row: dict[str, Any] | None) -> float | None:
    direct = _normalized_number(
        _first(row, "flu_rt", "change_rate", "등락률", "stck_prdy_ctrt")
    )
    if direct is not None:
        return round(direct, 4)
    current = _close_price(row)
    previous = _close_price(previous_row)
    if current is None or previous is None or previous <= 0:
        return None
    return round((current / previous - 1.0) * 100.0, 4)


def _trade_value_eok(row: dict[str, Any] | None) -> float | None:
    if not isinstance(row, dict):
        return None
    parsed = previous_trade_value_from_daily_row(row)
    value = to_number(parsed.get("prev_trade_value_eok"))
    return round(float(value), 4) if value is not None and value >= 0 else None


def _select_exact_and_previous(
    daily_rows: list[Any], target_date: str
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
                exact, _previous = _select_exact_and_previous(rows, target_date)
                if exact is not None:
                    return rows, source_name, None
                last_error = f"target date {target_date} missing for {registered_code}"
            else:
                last_error = f"daily rows missing for {registered_code}"
        except Exception as error:  # fail one code only
            last_error = f"{type(error).__name__}: {error}"
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return None, None, last_error


def build_portable_snapshot(
    codes_file: Path,
    limit: int,
    sleep_sec: float,
    target_date: str,
    market_phase: str,
) -> dict[str, Any]:
    path = Path(codes_file)
    codes = []
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

    for code in codes:
        rows, source_name, error_text = _request_daily_rows(
            token, code, target_date, max(0.0, float(sleep_sec or 0.0))
        )
        if not isinstance(rows, list) or not source_name:
            if len(errors) < 30:
                errors.append({"stock_code": code, "error": error_text or "no exact row"})
            continue

        exact, previous = _select_exact_and_previous(rows, target_date)
        ohlc = sf.base._row_ohlc(exact) if exact is not None else None
        if not isinstance(ohlc, dict):
            if len(errors) < 30:
                errors.append({"stock_code": code, "error": "invalid exact-date OHLC"})
            continue

        ohlc = dict(ohlc)
        ohlc.update(
            {
                "source": source_name,
                "date": target_date,
                "source_trading_date": target_date,
                "market_scope": "integrated_AL" if "_AL_" in source_name else "regular_KRX",
            }
        )
        ohlc_values[code] = ohlc
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

        price = _close_price(exact)
        change_rate = _change_rate(exact, previous) if exact is not None else None
        trade_value = _trade_value_eok(exact)
        previous_value = _trade_value_eok(previous)
        previous_date = _row_date(previous)
        if None in (price, change_rate, trade_value):
            if len(errors) < 30:
                errors.append({"stock_code": code, "error": "exact core board field missing"})
            continue

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
            "market_scope": ohlc["market_scope"],
            "source": source_name,
            "quality": "EXACT_DATE_REBUILT",
        }

    coverage = len(board_values) / len(codes) if codes else 0.0
    verified = bool(codes) and coverage >= max(0.1, min(1.0, PORTABLE_MIN_COVERAGE))
    return {
        "schema_version": 2,
        "source": "stockboard_v2_portable_ka10086_exact_date",
        "portable_policy_version": PORTABLE_POLICY_VERSION,
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
        "values": ohlc_values,
        "board_values": board_values,
    }


def fetch_ohlc_bootstrap(
    codes_file: Path,
    limit: int = 300,
    sleep_sec: float = 0.12,
):
    target_date, phase = sf.market_supply_target_context()
    path = Path(codes_file)
    return sf.coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka10086_portable_board_bundle",
        params={
            "codes_fingerprint": sf._file_fingerprint(path),
            "limit": int(limit or 300),
            "target_trading_date": target_date,
            "suffix_policy": "AL_first_regular_fallback",
            "portable_policy_version": PORTABLE_POLICY_VERSION,
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
            path, limit, sleep_sec, target_date, phase
        ),
    )


_original_inject_context_status = sf._inject_context_status


def _inject_context_status(payload):
    status = _original_inject_context_status(payload)
    status["portable_board_policy_version"] = PORTABLE_POLICY_VERSION
    status["context_entrypoint"] = "realtime_v2.context_snapshot_writer_portable"
    return status


sf.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
sf.base.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
sf._inject_context_status = _inject_context_status


if __name__ == "__main__":
    raise SystemExit(sf.main())
