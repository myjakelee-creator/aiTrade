from __future__ import annotations

from copy import deepcopy
from http import HTTPStatus
from typing import Any

from realtime_v2.common import event_age_sec, now_text


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _projection_status(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    statuses = _as_dict(manifest.get("projection_status"))
    return _as_dict(statuses.get(name))


def build_opening_load_payload(state, hub) -> dict[str, Any]:
    """Build one lightweight opening-load snapshot from already-published state.

    This function never calls ``state.snapshot()``, never submits a projection, and
    never performs TR/OpenAPI work. It reads only the existing State status, quote
    timestamps, BoardDataHub manifest, and the last completed Theme projection.
    """

    with state.lock:
        status = deepcopy(getattr(state, "status", {}) or {})
        quotes = [dict(row) for row in getattr(state, "quotes", {}).values()]

    manifest = hub.manifest() if hub is not None else {}
    collector_event = _as_dict(status.get("collector_status"))
    collector = _as_dict(collector_event.get("status"))
    sender = _as_dict(collector_event.get("sender_stats"))

    theme_status = _projection_status(manifest, "theme")
    detail_status = _projection_status(manifest, "theme_detail")
    strategy_status = _projection_status(manifest, "strategy")

    feature_version = _as_int(manifest.get("feature_version"))
    theme_completed = _as_int(theme_status.get("completed_feature_version"))
    detail_completed = _as_int(detail_status.get("completed_feature_version"))
    strategy_completed = _as_int(strategy_status.get("completed_feature_version"))

    row_source_counts: dict[str, int] = {}
    fresh_2s = fresh_5s = stale_over_5s = received_rows = 0
    for row in quotes:
        source = str(row.get("row_source") or "").strip()
        if not source:
            source = "realtime" if row.get("received_at") else "seed_universe"
        row_source_counts[source] = row_source_counts.get(source, 0) + 1
        received_at = row.get("received_at")
        if not received_at:
            continue
        received_rows += 1
        age = event_age_sec(received_at)
        if age is None:
            continue
        if age <= 2.0:
            fresh_2s += 1
        if age <= 5.0:
            fresh_5s += 1
        else:
            stale_over_5s += 1

    theme_projection = hub.projection_snapshot("theme") if hub is not None else None
    theme_payload = _as_dict(
        theme_projection.get("payload") if isinstance(theme_projection, dict) else None
    )
    performance = _as_dict(theme_payload.get("performance_breakdown"))
    leader_status = _as_dict(theme_payload.get("leader_selection_status"))

    collector_ready = bool(
        collector_event.get("collector_ready")
        or (
            collector.get("running")
            and collector.get("login_state") == "connected"
            and collector.get("realreg_succeeded") is True
        )
    )

    return {
        "schema_version": 1,
        "source": "stockboard_v2_opening_load_diagnostics",
        "ts": now_text(),
        "policy": {
            "read_only": True,
            "direct_tr_allowed": False,
            "direct_openapi_allowed": False,
            "state_snapshot_call_allowed": False,
            "projection_submit_allowed": False,
            "file_write_allowed": False,
        },
        "session": {
            "phase": status.get("metric_continuity_phase")
            or status.get("market_phase"),
            "reference_date": status.get("metric_continuity_reference_date"),
            "last_event_at": status.get("last_event_at"),
        },
        "collector": {
            "ready": collector_ready,
            "mode": collector.get("collector_mode")
            or collector_event.get("collector_mode"),
            "login_state": collector.get("login_state"),
            "realreg_succeeded": collector.get("realreg_succeeded"),
            "realreg_code_count": collector.get("realreg_code_count"),
            "realdata_received_count": collector.get("realdata_received_count"),
            "trade_event_received_count": collector.get(
                "trade_event_received_count"
            ),
            "trade_value_sample_count": collector.get("trade_value_sample_count"),
            "trade_value_sample_skip_count": collector.get(
                "trade_value_sample_skip_count"
            ),
            "trade_qty_read_count": collector.get("trade_qty_read_count"),
            "last_error": collector.get("last_error")
            or collector_event.get("sender_last_error"),
        },
        "sender": {
            "connected": sender.get("connected"),
            "flush_ms": sender.get("flush_ms"),
            "pending_trade_count": sender.get("pending_trade_count"),
            "pending_direct_count": sender.get("pending_direct_count"),
            "pending_flow_code_count": sender.get("pending_flow_code_count"),
            "pending_total_count": sender.get("pending_total_count"),
            "sent_count": sender.get("sent_count"),
            "sent_per_sec": sender.get("sent_per_sec"),
            "received_trade_count": sender.get("received_trade_count"),
            "coalesced_trade_overwrite_count": sender.get(
                "coalesced_trade_overwrite_count"
            ),
            "last_error": sender.get("last_error"),
        },
        "worker": {
            "event_count": status.get("event_count"),
            "trade_count": status.get("trade_count"),
            "event_log_queue_size": status.get("event_log_queue_size"),
            "event_log_dropped_count": status.get("event_log_dropped_count"),
            "event_log_last_error": status.get("event_log_last_error"),
            "stream_clients": status.get("stream_clients"),
            "theme_stream_clients": status.get("theme_stream_clients"),
            "universe_count": status.get("universe_count") or len(quotes),
            "received_row_count": received_rows,
            "fresh_price_rows_2s": fresh_2s,
            "fresh_price_rows_5s": fresh_5s,
            "stale_price_rows_over_5s": stale_over_5s,
            "row_source_counts": row_source_counts,
            "last_error": status.get("last_error"),
        },
        "hub": {
            "state_version": manifest.get("state_version"),
            "feature_version": feature_version,
            "feature_row_count": manifest.get("feature_row_count"),
            "feature_published_at": manifest.get("feature_published_at"),
        },
        "theme": {
            "pending_feature_version": theme_status.get(
                "pending_feature_version"
            ),
            "completed_feature_version": theme_completed,
            "feature_lag": max(0, feature_version - theme_completed),
            "build_inflight": theme_status.get("build_inflight"),
            "build_count": theme_status.get("build_count"),
            "publish_count": theme_status.get("publish_count"),
            "coalesced_request_count": theme_status.get(
                "coalesced_request_count"
            ),
            "stale_discard_count": theme_status.get("stale_discard_count"),
            "error_count": theme_status.get("error_count"),
            "last_build_ms": theme_status.get("last_build_ms"),
            "last_error": theme_status.get("last_error"),
            "calculate_ms": theme_payload.get("calculate_ms"),
            "aggregate_ms": performance.get("aggregate_ms"),
            "dual_rank_ms": performance.get("dual_rank_ms"),
            "leader_rank_ms": performance.get("leader_rank_ms"),
            "theme_count": theme_payload.get("theme_count"),
            "leader_theme_count": leader_status.get("theme_count"),
        },
        "theme_detail": {
            "selected_theme_id": detail_status.get("selected_theme_id"),
            "pending_feature_version": detail_status.get(
                "pending_feature_version"
            ),
            "completed_feature_version": detail_completed,
            "feature_lag": max(0, feature_version - detail_completed),
            "build_inflight": detail_status.get("build_inflight"),
            "error_count": detail_status.get("error_count"),
            "last_build_ms": detail_status.get("last_build_ms"),
            "last_error": detail_status.get("last_error"),
        },
        "strategy": {
            "pending_feature_version": strategy_status.get(
                "pending_feature_version"
            ),
            "completed_feature_version": strategy_completed,
            "feature_lag": max(0, feature_version - strategy_completed),
            "build_inflight": strategy_status.get("build_inflight"),
            "error_count": strategy_status.get("error_count"),
            "last_build_ms": strategy_status.get("last_build_ms"),
            "last_error": strategy_status.get("last_error"),
        },
    }


def install(base) -> None:
    """Expose one read-only opening-load endpoint on the existing worker."""

    handler_class = base.WebHandler
    if getattr(handler_class, "_stockboard_opening_load_diagnostics_installed", False):
        return

    original_do_get = handler_class.do_GET

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path != "/api/v2/hub/opening-load":
            return original_do_get(self)

        state = self.server.state
        hub = getattr(state, "board_data_hub", None)
        if hub is None:
            self._json(
                {"error": "board data hub unavailable"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        self._json(build_opening_load_payload(state, hub))

    handler_class.do_GET = patched_do_get
    handler_class._stockboard_opening_load_diagnostics_installed = True
