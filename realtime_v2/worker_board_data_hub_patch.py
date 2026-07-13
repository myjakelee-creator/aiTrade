from __future__ import annotations

import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.common import now_text
from realtime_v2.strategy_projection_engine import StrategyProjectionRuntime
from realtime_v2.theme_projection_engine import ThemeProjectionRuntime
from realtime_v2.tr_singleflight import get_shared_tr_coordinator


ROOT = Path(__file__).resolve().parents[1]


def install(base) -> None:
    """Attach one shared read model to the existing worker State/WebHandler.

    Install this before the opening-burst cache patch. The cache then captures the
    hub-wrapped heavy snapshot builder, so each expensive calculation is published
    once and all boards consume the same completed feature snapshot. Theme and
    Strategy projections run in separate latest-only background workers and perform
    no TR calls or candidate re-scoring.
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
        self.theme_projection_runtime = ThemeProjectionRuntime(self.board_data_hub)
        self.strategy_projection_runtime = StrategyProjectionRuntime(self.board_data_hub)
        self.theme_projection_runtime.start()
        self.strategy_projection_runtime.start()
        with self.lock:
            self.status["board_data_hub_enabled"] = True
            self.status["board_data_hub_direct_board_tr_allowed"] = False
            self.status["board_data_hub_html_calculation_allowed"] = False
            self.status["theme_projection_enabled"] = True
            self.status["theme_projection_input"] = (
                "board_data_hub_shared_feature_snapshot"
            )
            self.status["theme_projection_interval_ms"] = 1000
            self.status["theme_stream_clients"] = 0
            self.status["strategy_projection_enabled"] = True
            self.status["strategy_projection_input"] = (
                "board_data_hub_shared_feature_snapshot"
            )
            self.status["strategy_projection_candidate_rescore_allowed"] = False

    def patched_apply_event(self, event: dict[str, Any]) -> None:
        original_apply_event(self, event)
        event_type = str(event.get("type") or "unknown")
        if event_type not in {"trade", "orderbook", "close_metrics"}:
            return
        hub = getattr(self, "board_data_hub", None)
        if hub is None:
            return
        event_kwargs = (
            event.get("kwargs") if isinstance(event.get("kwargs"), dict) else {}
        )
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
                feature_version = hub.publish_feature_snapshot(payload)
                theme_runtime = getattr(self, "theme_projection_runtime", None)
                strategy_runtime = getattr(self, "strategy_projection_runtime", None)
                if theme_runtime is not None:
                    theme_runtime.submit(feature_version)
                if strategy_runtime is not None:
                    strategy_runtime.submit(feature_version)
                payload_status = payload.get("status")
                if isinstance(payload_status, dict):
                    payload_status["board_data_hub"] = hub.manifest()
                    payload_status["tr_singleflight"] = (
                        get_shared_tr_coordinator().status()
                    )
                    if theme_runtime is not None:
                        payload_status["theme_projection"] = theme_runtime.status()
                    if strategy_runtime is not None:
                        payload_status["strategy_projection"] = (
                            strategy_runtime.status()
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

    def theme_projection(hub):
        return hub.projection_snapshot("theme") if hub is not None else None

    def theme_payload(projection: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(projection, dict):
            return None
        payload = projection.get("payload")
        if not isinstance(payload, dict):
            return None
        result = dict(payload)
        result.update(
            {
                "projection": "theme",
                "projection_version": projection.get("projection_version"),
                "input_feature_version": projection.get("input_feature_version"),
                "published_at": projection.get("published_at"),
            }
        )
        return result

    def send_html(handler, path: Path) -> None:
        body = path.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def stream_theme(handler, query, hub) -> None:
        try:
            interval_ms = int((query.get("interval_ms") or ["1000"])[0])
        except (TypeError, ValueError):
            interval_ms = 1000
        interval_sec = max(1.0, min(5.0, interval_ms / 1000.0))
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()

        last_version = None
        last_sent_at = 0.0
        state = handler.server.state
        with state.lock:
            state.status["theme_stream_clients"] = (
                int(state.status.get("theme_stream_clients") or 0) + 1
            )
        try:
            while True:
                projection = theme_projection(hub)
                payload = theme_payload(projection)
                version = (
                    projection.get("projection_version")
                    if isinstance(projection, dict)
                    else None
                )
                now_mono = time.monotonic()
                should_send = (
                    payload is not None
                    and (version != last_version or now_mono - last_sent_at >= 5.0)
                )
                if should_send:
                    body = base.safe_json_dumps(payload)
                    handler.wfile.write(
                        f"event: themes\ndata: {body}\n\n".encode("utf-8")
                    )
                    handler.wfile.flush()
                    last_version = version
                    last_sent_at = now_mono
                time.sleep(interval_sec)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with state.lock:
                state.status["theme_stream_clients"] = max(
                    0, int(state.status.get("theme_stream_clients") or 1) - 1
                )

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        query = base.parse_qs(parsed.query)
        hub = getattr(self.server.state, "board_data_hub", None)

        if parsed.path in {"/theme", "/themeboard", "/themeboard.html"}:
            theme_html = ROOT / "docs" / "themeboard.html"
            if not theme_html.is_file():
                self._json(
                    {"error": "ThemeBoard HTML unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            send_html(self, theme_html)
            return

        if parsed.path == "/api/v2/hub/manifest":
            payload = (
                hub.manifest()
                if hub is not None
                else {
                    "schema_version": 1,
                    "source": "board_data_hub",
                    "enabled": False,
                    "ts": now_text(),
                }
            )
            payload["tr_singleflight"] = get_shared_tr_coordinator().status()
            self._json(payload)
            return

        if parsed.path == "/api/v2/hub/canonical":
            if hub is None:
                self._json(
                    {"error": "board data hub unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self._json(hub.canonical_snapshot(self.server.state, parse_limit(query)))
            return

        if parsed.path == "/api/v2/hub/features":
            if hub is None:
                self._json(
                    {"error": "board data hub unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self._json(hub.feature_snapshot(parse_limit(query)))
            return

        if parsed.path == "/api/v2/hub/theme/stream":
            if hub is None:
                self._json(
                    {"error": "board data hub unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            stream_theme(self, query, hub)
            return

        if parsed.path == "/api/v2/hub/theme/detail":
            if hub is None:
                self._json(
                    {"error": "board data hub unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            selected_id = str(
                (query.get("theme_id") or [""])[0] or ""
            ).strip()
            projection = theme_projection(hub)
            payload = theme_payload(projection)
            details = payload.get("details") if isinstance(payload, dict) else None
            detail = details.get(selected_id) if isinstance(details, dict) else None
            if not isinstance(detail, dict):
                self._json(
                    {
                        "error": "theme detail not found",
                        "theme_id": selected_id,
                        "projection_version": (
                            projection.get("projection_version")
                            if isinstance(projection, dict)
                            else None
                        ),
                    },
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            self._json(
                {
                    "schema_version": 2,
                    "source": "board_data_hub_theme_detail",
                    "theme_id": selected_id,
                    "projection_version": projection.get("projection_version"),
                    "input_feature_version": projection.get("input_feature_version"),
                    "published_at": projection.get("published_at"),
                    "theme": detail,
                }
            )
            return

        if parsed.path in {
            "/api/v2/hub/theme",
            "/api/v2/hub/strategy",
            "/api/v2/hub/projection",
        }:
            if hub is None:
                self._json(
                    {"error": "board data hub unavailable"},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            if parsed.path.endswith("/theme"):
                name = "theme"
            elif parsed.path.endswith("/strategy"):
                name = "strategy"
            else:
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
            if name == "theme":
                self._json(theme_payload(projection))
            else:
                self._json(projection)
            return

        return original_do_get(self)

    state_class.__init__ = patched_init
    state_class.apply_event = patched_apply_event
    state_class.snapshot = patched_snapshot
    base.WebHandler.do_GET = patched_do_get
    state_class._stockboard_board_data_hub_installed = True
