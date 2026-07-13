from __future__ import annotations

from http import HTTPStatus
from typing import Any

from realtime_v2.theme_selected_detail_runtime import ThemeSelectedDetailRuntime


def install(base) -> None:
    """Attach one selected-theme detail worker after the shared Hub patch."""

    state_class = base.State
    if getattr(state_class, "_stockboard_theme_selected_detail_installed", False):
        return

    original_init = state_class.__init__
    original_snapshot = state_class.snapshot
    original_do_get = base.WebHandler.do_GET

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        summary_runtime = getattr(self, "theme_projection_runtime", None)
        summary_worker = getattr(summary_runtime, "worker", None)
        summary_builder = getattr(summary_worker, "builder", None)
        hub = getattr(self, "board_data_hub", None)
        if hub is None or summary_builder is None:
            self.theme_selected_detail_runtime = None
            with self.lock:
                self.status["theme_selected_detail_enabled"] = False
                self.status["theme_selected_detail_last_error"] = (
                    "summary runtime or board data hub unavailable"
                )
            return
        runtime = ThemeSelectedDetailRuntime(
            hub,
            summary_builder,
            min_interval_ms=1000,
        )
        runtime.start()
        self.theme_selected_detail_runtime = runtime
        with self.lock:
            self.status["theme_selected_detail_enabled"] = True
            self.status["theme_selected_detail_queue"] = "latest_only_depth_1"
            self.status["theme_selected_detail_http_calculation_allowed"] = False
            self.status["theme_all_member_detail_generation_allowed"] = False

    def patched_snapshot(self, limit: int = 300) -> dict[str, Any]:
        payload = original_snapshot(self, limit)
        runtime = getattr(self, "theme_selected_detail_runtime", None)
        hub = getattr(self, "board_data_hub", None)
        if runtime is not None and hub is not None:
            feature_version = hub.borrow_feature_snapshot()[0]
            runtime.submit(feature_version)
            status = payload.get("status") if isinstance(payload, dict) else None
            if isinstance(status, dict):
                status["theme_selected_detail"] = runtime.status()
        return payload

    def detail_projection(hub):
        return hub.projection_snapshot("theme_detail") if hub is not None else None

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path == "/api/v2/hub/theme/detail/status":
            runtime = getattr(
                self.server.state, "theme_selected_detail_runtime", None
            )
            self._json(
                runtime.status()
                if runtime is not None
                else {
                    "enabled": False,
                    "status": "UNAVAILABLE",
                }
            )
            return

        if parsed.path != "/api/v2/hub/theme/detail":
            return original_do_get(self)

        query = base.parse_qs(parsed.query)
        selected_id = str((query.get("theme_id") or [""])[0] or "").strip()
        runtime = getattr(self.server.state, "theme_selected_detail_runtime", None)
        hub = getattr(self.server.state, "board_data_hub", None)
        if not selected_id:
            self._json(
                {"error": "theme_id is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if runtime is None or hub is None:
            self._json(
                {"error": "theme detail runtime unavailable"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        runtime.select(selected_id)
        projection = detail_projection(hub)
        raw_payload = (
            projection.get("payload")
            if isinstance(projection, dict)
            and isinstance(projection.get("payload"), dict)
            else None
        )
        ready = (
            isinstance(raw_payload, dict)
            and raw_payload.get("status") == "READY"
            and str(raw_payload.get("theme_id") or "") == selected_id
            and isinstance(raw_payload.get("theme"), dict)
        )
        if not ready:
            self._json(
                {
                    "schema_version": 1,
                    "source": "board_data_hub_theme_detail",
                    "status": "BUILDING",
                    "theme_id": selected_id,
                    "projection_version": (
                        projection.get("projection_version")
                        if isinstance(projection, dict)
                        else None
                    ),
                    "input_feature_version": (
                        projection.get("input_feature_version")
                        if isinstance(projection, dict)
                        else None
                    ),
                    "runtime": runtime.status(),
                    "policy": {
                        "http_calculation_allowed": False,
                        "request_action": "latest_only_selection_enqueue",
                    },
                },
                status=HTTPStatus.ACCEPTED,
            )
            return

        self._json(
            {
                "schema_version": 3,
                "source": "board_data_hub_theme_detail",
                "status": "READY",
                "theme_id": selected_id,
                "projection_version": projection.get("projection_version"),
                "input_feature_version": projection.get(
                    "input_feature_version"
                ),
                "published_at": projection.get("published_at"),
                "calculate_ms": raw_payload.get("calculate_ms"),
                "theme": raw_payload.get("theme"),
                "policy": raw_payload.get("policy"),
            }
        )

    state_class.__init__ = patched_init
    state_class.snapshot = patched_snapshot
    base.WebHandler.do_GET = patched_do_get
    state_class._stockboard_theme_selected_detail_installed = True
