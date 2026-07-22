from __future__ import annotations

"""Exact completed-day field policy for portable StockBoard snapshots.

The underlying portable writer keeps the proven request, candidate validation, and
single-flight lifecycle. This entrypoint changes completed-day field selection and
installs the resumable retry policy:

- price/OHLC close use historical close fields, never ``cur_prc``;
- change rate uses the exact row's official ``flu_rt`` when present;
- historical-close calculation is retained as a diagnostic and fallback;
- partial candidates survive transient failures and retry on the existing loop;
- retry identity follows the target trading date and parser contract, not the
  changing closed/weekend/holiday/premarket phase label.

No QAx, realtime FID, WebSocket, worker thread, or additional periodic request is
introduced.
"""

from typing import Any

from realtime_v2 import context_snapshot_writer_portable as portable

PORTABLE_PARSER_VERSION = "exact_daily_row_fields_v2"
RETRY_IDENTITY_VERSION = "target_date_policy_parser_v1"


def _install() -> None:
    if getattr(portable, "_portable_exact_fields_v2_installed", False):
        return
    portable._portable_v2_original_change_rate = portable._historical_change_rate
    portable._portable_v2_original_build = portable.build_portable_snapshot
    portable._portable_v2_original_inject_status = portable._inject_context_status

    portable.PORTABLE_PARSER_VERSION = PORTABLE_PARSER_VERSION

    def selected_change_rate(
        row: dict[str, Any] | None,
        previous_row: dict[str, Any] | None,
    ) -> float | None:
        computed = portable._portable_v2_original_change_rate(row, previous_row)
        direct_raw = (
            portable._first(
                row,
                "flu_rt",
                "change_rate",
                "등락률",
                "stck_prdy_ctrt",
            )
            if isinstance(row, dict)
            else None
        )
        direct = portable._normalized_number(direct_raw)
        selected = round(float(direct), 4) if direct is not None else computed
        source = (
            "exact_row_flu_rt"
            if direct is not None
            else "computed_from_historical_closes"
        )
        if isinstance(row, dict):
            row["_portable_selected_change_rate"] = selected
            row["_portable_computed_change_rate"] = computed
            row["_portable_change_rate_source"] = source
        return selected

    def diagnostic_sample(
        *,
        code: str,
        source_name: str,
        exact: dict[str, Any],
        selected_close: float,
        computed_change_rate: float,
        market_scope: str,
    ) -> dict[str, Any]:
        selected_rate = portable.to_number(
            exact.get("_portable_selected_change_rate")
        )
        if selected_rate is None:
            selected_rate = portable.to_number(computed_change_rate)
        computed_rate = portable.to_number(
            exact.get("_portable_computed_change_rate")
        )
        delta = (
            round(float(selected_rate) - float(computed_rate), 4)
            if selected_rate is not None and computed_rate is not None
            else None
        )
        return {
            "stock_code": code,
            "row_date": portable._row_date(exact),
            "raw_cur_prc": portable._first(exact, "cur_prc", "현재가"),
            "raw_close_pric": portable._first(
                exact, "close_pric", "close", "종가", "stck_clpr"
            ),
            "raw_flu_rt": portable._first(
                exact, "flu_rt", "change_rate", "등락률", "stck_prdy_ctrt"
            ),
            "selected_close": selected_close,
            "selected_change_rate": selected_rate,
            "change_rate_source": exact.get("_portable_change_rate_source"),
            "computed_change_rate": computed_rate,
            "change_rate_delta": delta,
            "query_code": portable._query_code_for_source(code, source_name),
            "source": source_name,
            "market_scope": market_scope,
        }

    def build_portable_snapshot(*args, **kwargs):
        payload = portable._portable_v2_original_build(*args, **kwargs)
        payload = dict(payload) if isinstance(payload, dict) else {}
        payload["schema_version"] = max(4, int(payload.get("schema_version") or 0))
        payload["source"] = (
            "stockboard_v2_portable_ka10086_exact_historical_fields_v2"
        )
        payload["portable_parser_version"] = PORTABLE_PARSER_VERSION

        diagnostics = payload.get("diagnostic_samples")
        diagnostics = diagnostics if isinstance(diagnostics, list) else []
        diagnostic_by_code = {
            str(item.get("stock_code") or ""): item
            for item in diagnostics
            if isinstance(item, dict)
        }
        board_values = payload.get("board_values")
        if isinstance(board_values, dict):
            for raw_code, raw_value in board_values.items():
                if not isinstance(raw_value, dict):
                    continue
                raw_value["portable_parser_version"] = PORTABLE_PARSER_VERSION
                diagnostic = diagnostic_by_code.get(str(raw_code)) or {}
                raw_value["change_rate_source"] = diagnostic.get(
                    "change_rate_source"
                )
                raw_value["computed_change_rate"] = diagnostic.get(
                    "computed_change_rate"
                )
                raw_value["change_rate_delta"] = diagnostic.get(
                    "change_rate_delta"
                )
                ohlc = raw_value.get("ohlc")
                if isinstance(ohlc, dict):
                    ohlc["portable_parser_version"] = PORTABLE_PARSER_VERSION
        return payload

    def inject_context_status(payload):
        status = portable._portable_v2_original_inject_status(payload)
        status["portable_board_parser_version"] = PORTABLE_PARSER_VERSION
        status["portable_board_retry_identity_version"] = RETRY_IDENTITY_VERSION
        status["context_entrypoint"] = (
            "realtime_v2.context_snapshot_writer_portable_v2"
        )
        status.setdefault("context_process_ready", True)
        status.setdefault("context_board_ready", False)
        return status

    portable._historical_change_rate = selected_change_rate
    portable._diagnostic_sample = diagnostic_sample
    portable.build_portable_snapshot = build_portable_snapshot
    portable._inject_context_status = inject_context_status
    portable.sf._inject_context_status = inject_context_status

    from realtime_v2 import context_snapshot_retry_patch as retry_patch

    def stable_retry_key(_portable, target_date: str, _phase: str) -> tuple[str, str]:
        return (
            _portable._date_digits(target_date),
            f"{_portable.PORTABLE_POLICY_VERSION}:{PORTABLE_PARSER_VERSION}",
        )

    retry_patch._key = stable_retry_key
    retry_patch.RETRY_IDENTITY_VERSION = RETRY_IDENTITY_VERSION
    retry_patch.install(portable, PORTABLE_PARSER_VERSION)
    portable._portable_exact_fields_v2_installed = True


sf = portable.sf
_attempted_refresh_keys = portable._attempted_refresh_keys


def build_portable_snapshot(*args, **kwargs):
    _install()
    return portable.build_portable_snapshot(*args, **kwargs)


def validate_portable_candidate(*args, **kwargs):
    _install()
    return portable.validate_portable_candidate(*args, **kwargs)


def fetch_ohlc_bootstrap(*args, **kwargs):
    _install()
    return portable.fetch_ohlc_bootstrap(*args, **kwargs)


def _snapshot_is_ready(*args, **kwargs):
    _install()
    return portable._snapshot_is_ready(*args, **kwargs)


if __name__ == "__main__":
    _install()
    raise SystemExit(portable.sf.main())
