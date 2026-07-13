from __future__ import annotations

from http import HTTPStatus
from typing import Any

from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.common import now_text
from realtime_v2.tr_singleflight import get_shared_tr_coordinator


def install(base) -> None:
    """Attach one shared read model to the existing worker State/WebHandler.

    Install this before the opening-burst cache patch. The cache then captures the
    hub-wrapped heavy snapshot builder, so each expensive calculation is published
    once and all future boards consume the same completed feature snapshot.
    """

    state_class = base.State
    if getattr(state_class, "_stockboard_board_data_hub_installed", False):
        return

    original_init = state_class.__init__
    original_apply_event = state_class.apply_event
    original_snapshot = state_class.snapshot
    original_apply_program_net = getattr(state_class, "apply_program_net_values", None)
    original_do_get = base.WebHandler.do_GET

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.board_data_hub = BoardDataHub()
        with self.lock:
            self.status["board_data_hub_enabled"] = True
            self.status["board_data_hub_direct_board_tr_allowed"] = False
            self.status["board_data_hub_html_calculation_allowed"] = False

    def patched_apply_event(self, event: dict[str, Any]) -> None:
        original_apply_event(self, event)
        event_type = str(event.get("type") or "unknown")
        if event_type not in {"trade", "orderbook", "close_metrics"}:
            return
        hub = getattr(self, "board_data_hub", None)
        if hub is None:
            return
        event_kwargs = event.get("kwargs") if isinstance(event.get("kwargs"), dict) else {}
        hub.mark_state_change(
            event_type=event_type,
            at=event.get("ts") or now_text(),
            stock_code=(
                event.get("stock_code")
                or event.get("received_code")
                or event_kwargs.get("stock_code")
            ),
        )

    def patched_snapshot(self, limit: int = 300) -> dict[str, Any]:
        payload = original_snapshot(self, limit)
        hub = getattr(self, "board_data_hub", None)
        if hub is not None and isinstance(payload, dict):
            try:
                hub.publish_feature_snapshot(payload)
                payload_status = payload.get("status")
                if isinstance(payload_status, dict):
                    payload_status["board_data_hub"] = hub.manifest()
                    payload_status["tr_singleflight"] = (
                        get_shared_tr_coordinator().status()
                    )
            except Exception as error:
                with self.lock:
                    self.status["board_data_hub_last_error"] = (
                        f"{type(error).__name__}: {error}"
                    )
        return payload

    if callable(original_apply_program_net):
        def patched_apply_program_net_values(
            self,
            values: dict[str, Any],
            source: str,
            status: str,
        ) -> int:
            updated = original_apply_program_net(self, values, source, status)
            if updated:
                hub = getattr(self, "board_data_hub", None)
                if hub is not None:
                    hub.mark_state_change(
                        event_type="program_net",
                        at=now_text(),
                    )
            return updated

        state_class.apply_program_net_values = patched_apply_program_net_values

    def parse_limit(query, default: int = 300) -> int:
        try:
            value = int((query.get("limit") or [str(default)])[0])
        except (TypeError, ValueError):
            value = default
        return max(1, min(1000, value))

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        query = base.parse_qs(parsed.query)
        hub = getattr(self.server.state, "board_data_hub", None)

        if parsed.path == "/api/v2/hub/manifest":
            payload = hub.manifest() if hub is not None else {
                "schema_version": 1,
                "source": "board_data_hub",
                "enabled": False,
                "ts": now_text(),
            }
            payload["tr_singleflight"] = get_shared_tr_coordinator().status()
            self._json(payload)
            return

        if parsed.path == "/api/v2/hub/canonical":
            if hub is None:
                self._json({"error": "board data hub unavailable"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._json(hub.canonical_snapshot(self.server.state, parse_limit(query)))
            return

        if parsed.path == "/api/v2/hub/features":
            if hub is None:
                self._json({"error": "board data hub unavailable"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._json(hub.feature_snapshot(parse_limit(query)))
            return

        if parsed.path == "/api/v2/hub/projection":
            if hub is None:
                self._json({"error": "board data hub unavailable"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            name = str((query.get("name") or [""])[0] or "").strip().lower()
            projection = hub.projection_snapshot(name)
            if projection is None:
                self._json(
                    {
                        "error": "projection not published",
                        "projection": name,
                        "manifest": hub.manifest(),
                    },
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            self._json(projection)
            return

        return original_do_get(self)

    state_class.__init__ = patched_init
    state_class.apply_event = patched_apply_event
    state_class.snapshot = patched_snapshot
    base.WebHandler.do_GET = patched_do_get
    state_class._stockboard_board_data_hub_installed = True
