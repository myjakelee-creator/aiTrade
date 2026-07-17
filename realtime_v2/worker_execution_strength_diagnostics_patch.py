from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, to_number
from realtime_v2.execution_strength_alias_patch import execution_source_trusted

PATCH_VERSION = "execution_strength_diagnostics_v1"
SAMPLE_LIMIT = 10


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _latest_timestamp(values: list[Any]) -> str | None:
    latest_text: str | None = None
    latest_key: float | str | None = None
    latest_is_parsed = False
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            parsed_key: float | str = datetime.fromisoformat(
                text.replace("Z", "+00:00")
            ).timestamp()
            parsed = True
        except (OSError, OverflowError, TypeError, ValueError):
            parsed_key = text
            parsed = False
        if latest_key is None:
            latest_key = parsed_key
            latest_text = text
            latest_is_parsed = parsed
            continue
        if parsed and not latest_is_parsed:
            latest_key = parsed_key
            latest_text = text
            latest_is_parsed = True
            continue
        if parsed == latest_is_parsed and parsed_key > latest_key:
            latest_key = parsed_key
            latest_text = text
    return latest_text


def _rank_key(row: dict[str, Any]) -> tuple[int, str]:
    rank = _number(row.get("rank"))
    return (
        int(rank) if rank is not None and rank > 0 else 999999,
        normalize_code(row.get("stock_code")),
    )


def _collector_queue(status: dict[str, Any]) -> int | None:
    collector = status.get("collector_status")
    collector = collector if isinstance(collector, dict) else {}
    sender = collector.get("sender_stats")
    sender = sender if isinstance(sender, dict) else {}
    number = _number(sender.get("pending_total_count"))
    return None if number is None else int(number)


def build_execution_strength_diagnostics(
    rows: list[dict[str, Any]] | None,
    status: dict[str, Any] | None,
) -> dict[str, Any]:
    source_rows = [row for row in (rows or []) if isinstance(row, dict)]
    state = status if isinstance(status, dict) else {}
    expected_date = _date_digits(
        state.get("metric_session_state_date")
        or state.get("market_trading_date")
        or state.get("approved_pipeline_trading_date")
    )

    execution_positive = 0
    execution_trusted = 0
    execution_untrusted = 0
    execution_date_mismatch = 0
    strength5_positive = 0
    same_value = 0
    observed_times: list[Any] = []

    for row in source_rows:
        execution = _positive(row.get("execution_strength"))
        strength5 = _positive(row.get("strength_5m"))
        trusted = execution_source_trusted(row)
        if execution is not None:
            execution_positive += 1
            if trusted:
                execution_trusted += 1
                observed_times.extend(
                    (
                        row.get("execution_strength_received_at"),
                        row.get("execution_strength_updated_at"),
                        row.get("ui_execution_strength_observed_at"),
                    )
                )
                source_date = _date_digits(
                    row.get("execution_source_trading_date")
                    or row.get("ui_execution_source_trading_date")
                )
                if expected_date and source_date and source_date != expected_date:
                    execution_date_mismatch += 1
            else:
                execution_untrusted += 1
        if strength5 is not None:
            strength5_positive += 1
        if (
            execution is not None
            and strength5 is not None
            and abs(execution - strength5) <= 0.0001
        ):
            same_value += 1

    samples: list[dict[str, Any]] = []
    for row in sorted(source_rows, key=_rank_key)[:SAMPLE_LIMIT]:
        samples.append(
            {
                "rank": row.get("rank"),
                "stock_code": normalize_code(row.get("stock_code")),
                "stock_name": row.get("stock_name"),
                "execution_strength": row.get("execution_strength"),
                "execution_strength_source": row.get("execution_strength_source"),
                "execution_source_trading_date": row.get(
                    "execution_source_trading_date"
                )
                or row.get("ui_execution_source_trading_date"),
                "execution_strength_observed_at": row.get(
                    "execution_strength_received_at"
                )
                or row.get("execution_strength_updated_at")
                or row.get("ui_execution_strength_observed_at"),
                "strength_5m": row.get("strength_5m"),
                "strength_source": row.get("strength_source"),
                "strength_source_trading_date": row.get(
                    "strength_source_trading_date"
                )
                or row.get("strength5_source_trading_date")
                or row.get("ui_strength_source_trading_date"),
            }
        )

    return {
        "version": PATCH_VERSION,
        "row_count": len(source_rows),
        "expected_trading_date": expected_date or None,
        "execution_positive_count": execution_positive,
        "execution_trusted_fid228_count": execution_trusted,
        "execution_untrusted_positive_count": execution_untrusted,
        "execution_source_date_mismatch_count": execution_date_mismatch,
        "execution_visible_count": execution_positive,
        "strength5_visible_count": strength5_positive,
        "execution_strength5_same_value_count": same_value,
        "last_fid228_received_at": _latest_timestamp(observed_times),
        "execution_hidden_date_count": int(
            state.get("five_metric_execution_date_count") or 0
        ),
        "execution_hidden_source_count": int(
            state.get("five_metric_execution_source_count") or 0
        ),
        "execution_hidden_stale_count": int(
            state.get("five_metric_execution_stale_count") or 0
        ),
        "websocket_status": state.get("realtime_strength_ws_status"),
        "websocket_selected_count": int(
            state.get("realtime_strength_ws_selected_count") or 0
        ),
        "collector_queue": _collector_queue(state),
        "worker_queue": int(state.get("event_log_queue_size") or 0),
        "drop_count": int(state.get("dropped_trade_count") or 0),
        "logdrop_count": int(state.get("event_log_dropped_count") or 0),
        "source_contract_ok": execution_untrusted == 0,
        "samples": samples,
    }


def install(base) -> None:
    """Expose read-only FID228/strength5 diagnostics through the existing snapshot.

    No collector, QAx registration, FID, REST request, WebSocket connection, thread,
    persistence loop or browser calculation is added.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_execution_strength_diagnostics_installed",
        False,
    ):
        return

    original_snapshot = state_class.snapshot

    def snapshot(self, limit: int = 300):
        payload = original_snapshot(self, limit)
        if not isinstance(payload, dict):
            return payload
        status = payload.get("status")
        if not isinstance(status, dict):
            status = {}
            payload["status"] = status
        rows = payload.get("rows")
        diagnostics = build_execution_strength_diagnostics(
            rows if isinstance(rows, list) else [],
            status,
        )
        payload["execution_strength_diagnostics"] = deepcopy(diagnostics)
        status.update(
            {
                "execution_strength_diagnostics_installed": True,
                "execution_strength_diagnostics_version": PATCH_VERSION,
                "execution_diag_execution_positive_count": diagnostics[
                    "execution_positive_count"
                ],
                "execution_diag_trusted_fid228_count": diagnostics[
                    "execution_trusted_fid228_count"
                ],
                "execution_diag_untrusted_positive_count": diagnostics[
                    "execution_untrusted_positive_count"
                ],
                "execution_diag_strength5_visible_count": diagnostics[
                    "strength5_visible_count"
                ],
                "execution_diag_same_value_count": diagnostics[
                    "execution_strength5_same_value_count"
                ],
                "execution_diag_last_fid228_received_at": diagnostics[
                    "last_fid228_received_at"
                ],
                "execution_diag_source_contract_ok": diagnostics[
                    "source_contract_ok"
                ],
            }
        )
        return payload

    state_class.snapshot = snapshot
    state_class._stockboard_execution_strength_diagnostics_installed = True
