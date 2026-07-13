from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def _status_identity(event: dict[str, Any]) -> tuple[str, int, float | None]:
    instance = str(event.get("collector_instance_id") or "")
    try:
        seq = int(event.get("collector_status_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return instance, seq, _timestamp(event.get("ts"))


def _is_older_status(incoming: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if not isinstance(current, dict):
        return False

    incoming_instance, incoming_seq, incoming_ts = _status_identity(incoming)
    current_instance, current_seq, current_ts = _status_identity(current)

    if incoming_instance and current_instance and incoming_instance == current_instance:
        if incoming_seq and current_seq:
            return incoming_seq <= current_seq
        if incoming_ts is not None and current_ts is not None:
            return incoming_ts <= current_ts
        return False

    # A delayed status from an older collector instance must not replace the status
    # of the current instance. Timestamps are the cross-instance fallback.
    if incoming_ts is not None and current_ts is not None:
        return incoming_ts <= current_ts
    return False


def install(base) -> None:
    """Keep worker liveness and collector status monotonic.

    Worker `last_event_at` previously copied source event timestamps directly, so a
    delayed/retried event made the clock move backwards. Collector heartbeats were
    also accepted unconditionally, allowing old counter snapshots to overwrite new
    ones. Record arrival time separately and reject stale collector-status events.
    """

    state_class = base.State
    if getattr(state_class, "_stockboard_event_freshness_installed", False):
        return

    original_apply_event = state_class.apply_event

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        source_ts = event.get("ts")
        arrived_at = base.now_text()

        with self.lock:
            before_trade_count = int(self.status.get("trade_count") or 0)
            current_status = self.status.get("collector_status")
            if event_type in {"collector_status", "collector_heartbeat", "register_result"}:
                if _is_older_status(event, current_status):
                    self.status["event_count"] = int(self.status.get("event_count") or 0) + 1
                    self.status["last_event_at"] = arrived_at
                    self.status["last_event_received_at"] = arrived_at
                    self.status["last_event_source_at"] = source_ts
                    self.status["collector_status_stale_drop_count"] = int(
                        self.status.get("collector_status_stale_drop_count") or 0
                    ) + 1
                    self.status["collector_status_last_dropped_at"] = arrived_at
                    self.status["collector_status_last_dropped_source_at"] = source_ts
                    self.status["collector_status_last_dropped_instance_id"] = event.get(
                        "collector_instance_id"
                    )
                    self.status["collector_status_last_dropped_seq"] = event.get(
                        "collector_status_seq"
                    )
                    return

        original_apply_event(self, event)

        with self.lock:
            after_trade_count = int(self.status.get("trade_count") or 0)
            self.status["last_event_at"] = arrived_at
            self.status["last_event_received_at"] = arrived_at
            self.status["last_event_source_at"] = source_ts

            if event_type in {"collector_status", "collector_heartbeat", "register_result"}:
                self.status["collector_status_accepted_at"] = arrived_at
                self.status["collector_status_accepted_source_at"] = source_ts
                self.status["collector_status_accepted_instance_id"] = event.get(
                    "collector_instance_id"
                )
                self.status["collector_status_accepted_seq"] = event.get(
                    "collector_status_seq"
                )

            if event_type == "trade":
                if after_trade_count > before_trade_count:
                    self.status["last_trade_event_received_at"] = arrived_at
                    self.status["last_trade_event_source_at"] = source_ts
                else:
                    self.status["last_rejected_trade_received_at"] = arrived_at
                    self.status["last_rejected_trade_source_at"] = source_ts

    state_class.apply_event = apply_event
    state_class._stockboard_event_freshness_installed = True
