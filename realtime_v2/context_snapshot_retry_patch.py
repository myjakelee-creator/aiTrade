from __future__ import annotations

"""Resumable retry policy for portable closed-session StockBoard snapshots."""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

RETRY_BASE_SEC = max(30.0, float(os.getenv("STOCKBOARD_PORTABLE_RETRY_BASE_SEC", "60")))
RETRY_MAX_SEC = max(RETRY_BASE_SEC, float(os.getenv("STOCKBOARD_PORTABLE_RETRY_MAX_SEC", "300")))
CHECKPOINT_EVERY = max(1, int(os.getenv("STOCKBOARD_PORTABLE_CHECKPOINT_EVERY", "10")))

_retry_state: dict[tuple[str, str], dict[str, Any]] = {}


def _codes(portable, path: Path, limit: int) -> list[str]:
    if not path.is_file():
        return []
    values = [
        portable.normalize_code(line.strip())
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if portable.normalize_code(line.strip())
    ]
    return values[: max(1, int(limit or 300))]


def _partial_candidate(portable, parser_version: str, target_date: str) -> dict[str, Any]:
    payload = portable._read_json(portable._candidate_path())
    if not isinstance(payload, dict):
        return {}
    if payload.get("portable_policy_version") != portable.PORTABLE_POLICY_VERSION:
        return {}
    if payload.get("portable_parser_version") != parser_version:
        return {}
    source_date = portable._date_digits(
        payload.get("source_trading_date") or payload.get("trading_date")
    )
    return payload if source_date == portable._date_digits(target_date) else {}


def _valid_rows(portable, parser_version: str, payload: dict[str, Any], target_date: str, requested: list[str]) -> dict[str, dict[str, Any]]:
    requested_set = set(requested)
    rows = payload.get("board_values")
    if not isinstance(rows, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_code, raw_row in rows.items():
        code = portable.normalize_code(raw_code)
        if not code or code not in requested_set or not isinstance(raw_row, dict):
            continue
        if raw_row.get("portable_parser_version") != parser_version:
            continue
        date_keys = (
            "source_trading_date",
            "price_trading_date",
            "change_rate_trading_date",
            "trade_value_trading_date",
            "ohlc_trading_date",
        )
        if any(portable._date_digits(raw_row.get(key)) != portable._date_digits(target_date) for key in date_keys):
            continue
        if portable.to_number(raw_row.get("price")) is None:
            continue
        if portable.to_number(raw_row.get("change_rate")) is None:
            continue
        if portable.to_number(raw_row.get("trade_value_eok")) is None:
            continue
        if not isinstance(raw_row.get("ohlc"), dict):
            continue
        result[code] = dict(raw_row)
    return result


def _payload(portable, parser_version: str, requested: list[str], rows: dict[str, dict[str, Any]], target_date: str, phase: str, errors: list[dict[str, str]] | None = None, diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    requested_count = len(requested)
    coverage = len(rows) / requested_count if requested_count else 0.0
    minimum = max(0.1, min(1.0, portable.PORTABLE_MIN_COVERAGE))
    values: dict[str, dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    for code, row in rows.items():
        ohlc = row.get("ohlc")
        if isinstance(ohlc, dict):
            values[code] = dict(ohlc)
        source = str(row.get("source") or "")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "schema_version": 4,
        "source": "stockboard_v2_portable_ka10086_incremental_v2",
        "portable_policy_version": portable.PORTABLE_POLICY_VERSION,
        "portable_parser_version": parser_version,
        "ts": portable.now_text(),
        "trading_date": target_date,
        "source_trading_date": target_date,
        "market_phase": phase,
        "market_scope": "integrated_AL_regular_fallback",
        "count": len(values),
        "board_value_count": len(rows),
        "requested_count": requested_count,
        "coverage": round(coverage, 4),
        "verified": bool(requested_count) and coverage >= minimum,
        "minimum_coverage": portable.PORTABLE_MIN_COVERAGE,
        "source_counts": source_counts,
        "error_count": len(errors or []),
        "errors": list(errors or [])[:30],
        "diagnostic_samples": list(diagnostics or [])[: portable.DIAGNOSTIC_SAMPLE_LIMIT],
        "values": values,
        "board_values": rows,
    }


def _write_progress(portable, status: dict[str, Any], payload: dict[str, Any], refresh_status: str, attempt: int, last_error: str | None = None, next_retry_at: str | None = None) -> None:
    requested = int(portable.to_number(payload.get("requested_count")) or 0)
    completed = int(portable.to_number(payload.get("board_value_count")) or 0)
    status.update(
        {
            "context_process_ready": True,
            "context_board_ready": refresh_status == "exact_ready",
            "portable_board_refresh_status": refresh_status,
            "portable_board_target_trading_date": payload.get("source_trading_date"),
            "portable_board_market_phase": payload.get("market_phase"),
            "portable_board_completed_count": completed,
            "portable_board_requested_count": requested,
            "portable_board_pending_count": max(0, requested - completed),
            "portable_board_refresh_count": completed,
            "portable_board_refresh_coverage": payload.get("coverage"),
            "portable_board_retry_attempt": attempt,
            "portable_board_last_error": last_error,
            "portable_board_next_retry_at": next_retry_at,
            "portable_board_candidate_path": str(portable._candidate_path()),
        }
    )
    if last_error is None:
        status.pop("portable_board_refresh_error", None)
    else:
        status["portable_board_refresh_error"] = last_error
    portable.sf.write_status(status)


def _build(portable, parser_version: str, codes_file: Path, limit: int, sleep_sec: float, target_date: str, phase: str, status: dict[str, Any], attempt: int) -> dict[str, Any]:
    requested = _codes(portable, Path(codes_file), limit)
    prior = _partial_candidate(portable, parser_version, target_date)
    rows = _valid_rows(portable, parser_version, prior, target_date, requested)
    diagnostics = [item for item in (prior.get("diagnostic_samples") or []) if isinstance(item, dict)]
    pending = [code for code in requested if code not in rows]
    errors: list[dict[str, str]] = []

    current = _payload(portable, parser_version, requested, rows, target_date, phase, diagnostics=diagnostics)
    portable.sf._atomic_write(portable._candidate_path(), current)
    _write_progress(portable, status, current, "building", attempt)
    if not pending:
        return current

    token = portable.issue_access_token()
    for index, code in enumerate(pending, start=1):
        daily_rows, source_name, error_text = portable._request_daily_rows(
            token, code, target_date, max(0.0, float(sleep_sec or 0.0))
        )
        if not isinstance(daily_rows, list) or not source_name:
            errors.append({"stock_code": code, "error": error_text or "no exact historical row"})
        else:
            exact, previous = portable._select_exact_and_previous(daily_rows, target_date)
            ohlc_core = portable._historical_ohlc(exact)
            price = portable._historical_close(exact)
            change_rate = portable._historical_change_rate(exact, previous)
            trade_value = portable._trade_value_eok(exact)
            previous_value = portable._trade_value_eok(previous)
            previous_date = portable._row_date(previous)
            if exact is None or previous is None or ohlc_core is None or price is None or change_rate is None or trade_value is None:
                errors.append({"stock_code": code, "error": "historical exact core field missing"})
            else:
                market_scope = "integrated_AL" if source_name.startswith("ka10086_AL") else "regular_KRX"
                ohlc = {
                    **ohlc_core,
                    "source": source_name,
                    "date": target_date,
                    "source_trading_date": target_date,
                    "market_scope": market_scope,
                    "portable_parser_version": parser_version,
                }
                diagnostic = portable._diagnostic_sample(
                    code=code,
                    source_name=source_name,
                    exact=exact,
                    selected_close=price,
                    computed_change_rate=change_rate,
                    market_scope=market_scope,
                )
                if len(diagnostics) < portable.DIAGNOSTIC_SAMPLE_LIMIT:
                    diagnostics.append(diagnostic)
                rows[code] = {
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
                    "portable_parser_version": parser_version,
                    "change_rate_source": diagnostic.get("change_rate_source"),
                    "computed_change_rate": diagnostic.get("computed_change_rate"),
                    "change_rate_delta": diagnostic.get("change_rate_delta"),
                }

        if index % CHECKPOINT_EVERY == 0 or index == len(pending):
            current = _payload(portable, parser_version, requested, rows, target_date, phase, errors, diagnostics)
            portable.sf._atomic_write(portable._candidate_path(), current)
            _write_progress(portable, status, current, "building", attempt, errors[-1]["error"] if errors else None)
    return current


def _delay(attempt: int) -> float:
    return min(RETRY_MAX_SEC, RETRY_BASE_SEC * (2 ** max(0, int(attempt) - 1)))


def _key(portable, target_date: str, phase: str) -> tuple[str, str]:
    return portable._date_digits(target_date), str(phase or "unknown")


def _wait(portable, status: dict[str, Any], payload: dict[str, Any], key: tuple[str, str], attempt: int, error_text: str) -> None:
    delay = _delay(attempt)
    next_retry = datetime.now() + timedelta(seconds=delay)
    state = {
        "attempt": attempt,
        "next_retry_mono": time.monotonic() + delay,
        "next_retry_at": next_retry.isoformat(timespec="seconds"),
        "last_error": error_text,
    }
    _retry_state[key] = state
    _write_progress(portable, status, payload, "retry_wait", attempt, error_text, state["next_retry_at"])


def install(portable, parser_version: str) -> None:
    if getattr(portable, "_portable_retry_policy_v1_installed", False):
        return

    original_active_fetch = portable._original_active_fetch_ohlc_bootstrap
    original_run_cycle = portable._original_run_cycle

    def fetch_ohlc_bootstrap(codes_file: Path, limit: int = 300, sleep_sec: float = 0.12):
        target_date, phase = portable.sf.market_supply_target_context()
        path = Path(codes_file)
        portable._last_bootstrap_args = (path, int(limit or 300), float(sleep_sec or 0.0))
        if phase in portable.ACTIVE_PHASES:
            return original_active_fetch(path, limit, sleep_sec)

        key = _key(portable, target_date, phase)
        attempt = int((_retry_state.get(key) or {}).get("attempt") or 0) + 1
        status = portable._read_json(Path(portable.sf.base.STATUS_FILE)) or {}
        try:
            current = _build(portable, parser_version, path, limit, sleep_sec, target_date, phase, status, attempt)
            valid, reason = portable.validate_portable_candidate(current, target_date)
            if not valid:
                _wait(portable, status, current, key, attempt, f"portable candidate rejected: {reason}")
                raise RuntimeError(f"portable candidate rejected: {reason}")
            portable.sf._atomic_write(portable.sf.base.OHLC_SNAPSHOT_FILE, current)
            _retry_state.pop(key, None)
            _write_progress(portable, status, current, "exact_ready", attempt)
            return current
        except Exception as error:
            if key not in _retry_state:
                requested = _codes(portable, path, limit)
                partial = _valid_rows(portable, parser_version, _partial_candidate(portable, parser_version, target_date), target_date, requested)
                current = _payload(portable, parser_version, requested, partial, target_date, phase)
                _wait(portable, status, current, key, attempt, str(error))
            raise

    def run_cycle(status: dict[str, Any]) -> None:
        original_run_cycle(status)
        target_date, phase = portable.sf.market_supply_target_context()
        status["portable_board_target_trading_date"] = target_date
        status["portable_board_market_phase"] = phase
        status["portable_board_parser_version"] = parser_version
        status["portable_board_candidate_path"] = str(portable._candidate_path())
        status["context_process_ready"] = True

        if phase in portable.ACTIVE_PHASES:
            status["portable_board_refresh_status"] = "active_session_initial_only"
            status["context_board_ready"] = True
            status["portable_board_next_retry_at"] = None
            portable.sf.write_status(status)
            return

        if portable._snapshot_is_ready(target_date):
            current = portable._read_json(Path(portable.sf.base.OHLC_SNAPSHOT_FILE)) or {}
            _retry_state.pop(_key(portable, target_date, phase), None)
            _write_progress(portable, status, current, "exact_ready", int(status.get("portable_board_retry_attempt") or 0))
            return

        if portable._last_bootstrap_args is None:
            status["portable_board_refresh_status"] = "bootstrap_arguments_unavailable"
            status["context_board_ready"] = False
            portable.sf.write_status(status)
            return

        key = _key(portable, target_date, phase)
        retry = _retry_state.get(key) or {}
        if float(retry.get("next_retry_mono") or 0.0) > time.monotonic():
            current = _partial_candidate(portable, parser_version, target_date)
            _write_progress(
                portable,
                status,
                current,
                "retry_wait",
                int(retry.get("attempt") or 0),
                str(retry.get("last_error") or ""),
                str(retry.get("next_retry_at") or ""),
            )
            return

        path, limit, sleep_sec = portable._last_bootstrap_args
        attempt = int(retry.get("attempt") or 0) + 1
        try:
            current = _build(portable, parser_version, path, limit, sleep_sec, target_date, phase, status, attempt)
            valid, reason = portable.validate_portable_candidate(current, target_date)
            if valid:
                portable.sf._atomic_write(portable.sf.base.OHLC_SNAPSHOT_FILE, current)
                _retry_state.pop(key, None)
                _write_progress(portable, status, current, "exact_ready", attempt)
                return
            _wait(portable, status, current, key, attempt, f"portable candidate rejected: {reason}")
        except Exception as error:
            requested = _codes(portable, path, limit)
            partial = _valid_rows(portable, parser_version, _partial_candidate(portable, parser_version, target_date), target_date, requested)
            current = _payload(portable, parser_version, requested, partial, target_date, phase)
            _wait(portable, status, current, key, attempt, str(error))

    portable.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
    portable.sf.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
    portable.sf.base.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
    portable._run_cycle = run_cycle
    portable.sf._run_cycle = run_cycle
    portable._portable_retry_policy_v1_installed = True
