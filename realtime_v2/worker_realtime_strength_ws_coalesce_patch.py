from __future__ import annotations

import time
from typing import Any


def install(base) -> None:
    """Apply the latest S1 FID228 value at most once per second.

    The underlying WebSocket connection and thread are unchanged. This wrapper only
    keeps the newest event observed inside the configured apply interval and rejects
    malformed timestamps from the display-fresh path.
    """

    import realtime_v2.worker_realtime_strength_ws_patch as module

    updater_class = module.RealtimeStrengthWebSocket
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        updater_class, "_stockboard_ws_coalesce_installed", False
    ):
        return

    original_rows = state_class.rows

    def consume(
        self,
        connection,
        *,
        code: str,
        query_code: str,
        phase: str,
    ) -> None:
        receive_timeout = max(
            0.5,
            float(self.ws_config.get("receive_timeout_sec") or 2),
        )
        apply_interval = max(
            0.2,
            float(self.ws_config.get("apply_interval_ms") or 1000) / 1000.0,
        )
        pending_event: dict[str, Any] | None = None

        def flush_pending() -> None:
            nonlocal pending_event
            if pending_event is None:
                return
            now_mono = time.monotonic()
            if now_mono - self.last_applied_mono < apply_interval:
                return
            event = pending_event
            pending_event = None
            self.last_applied_mono = now_mono
            changed = self.state.apply_realtime_strength_ws(event)
            self.apply_count += 1
            if changed:
                self.changed_count += 1
            else:
                self.unchanged_count += 1
            self._status(
                realtime_strength_ws_status="ok",
                realtime_strength_ws_last_event_at=module.now_text(),
                realtime_strength_ws_last_source_time=event.get(
                    "execution_strength_source_time"
                ),
                realtime_strength_ws_last_value=event.get("execution_strength"),
                realtime_strength_ws_last_error=None,
            )

        self._status(
            realtime_strength_ws_status="subscribed",
            realtime_strength_ws_backend=connection.backend,
            realtime_strength_ws_selected_code=code,
            realtime_strength_ws_selected_source=self.selected_source,
            realtime_strength_ws_query_code=query_code,
            realtime_strength_ws_market_phase=phase,
            realtime_strength_ws_last_error=None,
            realtime_strength_ws_subscribed_at=module.now_text(),
            realtime_strength_ws_coalesce_mode="latest_value_1hz",
        )

        while not self.stop_event.is_set():
            if self._selection_changed(code, phase):
                flush_pending()
                self._status(realtime_strength_ws_status="resubscribe_required")
                return
            try:
                raw_message = connection.recv(receive_timeout)
            except TimeoutError:
                flush_pending()
                continue
            message = module._decode_message(raw_message)
            if module._is_ping(message):
                connection.send(message)
                flush_pending()
                continue
            if (
                isinstance(message, dict)
                and str(message.get("trnm") or "").upper() == "REG"
            ):
                return_code = int(module._number(message.get("return_code")) or 0)
                if return_code != 0:
                    raise RuntimeError(f"WebSocket REG failed: {message}")
                flush_pending()
                continue

            for event in module.parse_realtime_strength_message(message):
                if event.get("stock_code") != code:
                    continue
                self.event_count += 1
                pending_event = event
            flush_pending()

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        for row in result:
            if not isinstance(row, dict):
                continue
            if str(row.get("execution_strength_source") or "") != module.WS_SOURCE:
                continue
            received_at = row.get("execution_strength_received_at") or row.get(
                "execution_strength_updated_at"
            )
            age = module._iso_age_sec(received_at)
            if age is not None:
                continue
            row["execution_strength_legacy_snapshot"] = row.get(
                "execution_strength"
            )
            row.pop("execution_strength", None)
            row.pop("last_valid_execution_strength", None)
            row["execution_strength_available"] = False
            row["execution_strength_status"] = "invalid_realtime_timestamp"
        return result

    updater_class._consume = consume
    state_class.rows = rows
    updater_class._stockboard_ws_coalesce_installed = True
