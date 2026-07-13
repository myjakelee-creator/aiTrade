from __future__ import annotations

import threading
from typing import Any

from realtime_v2 import theme_projection_engine as theme_projection_module
from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.board_projection_runtime import LatestOnlyProjectionWorker
from realtime_v2.theme_leader_detail_display_patch import (
    install as install_theme_leader_detail_display,
)
from realtime_v2.theme_leader_selection_patch import (
    install as install_theme_leader_selection,
)


# The lightweight summary split has already been installed before this module is
# imported. Install the leader model before State creates the shared runtimes so
# summary leaders and selected-detail ordering use the same server calculation.
install_theme_leader_selection(theme_projection_module)
install_theme_leader_detail_display(theme_projection_module)


class ThemeSelectedDetailBuilder:
    def __init__(self, summary_builder) -> None:
        self.summary_builder = summary_builder
        self._lock = threading.RLock()
        self._selected_theme_id = ""

    def select(self, theme_id: str) -> bool:
        selected = str(theme_id or "").strip()
        if not selected:
            return False
        with self._lock:
            changed = selected != self._selected_theme_id
            self._selected_theme_id = selected
            return changed

    def selected(self) -> str:
        with self._lock:
            return self._selected_theme_id

    def __call__(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        return self.summary_builder.build_selected_detail(
            feature_version,
            self.selected(),
            rows,
            meta,
        )


class ThemeSelectedDetailRuntime:
    """Latest-only worker for one selected ThemeBoard detail."""

    def __init__(
        self,
        hub: BoardDataHub,
        summary_builder,
        min_interval_ms: int = 1000,
    ) -> None:
        self.hub = hub
        self.builder = ThemeSelectedDetailBuilder(summary_builder)
        self.worker = LatestOnlyProjectionWorker(
            name="theme_detail",
            hub=hub,
            builder=self.builder,
            min_interval_ms=min_interval_ms,
        )

    def start(self) -> None:
        self.worker.start()

    def stop(self) -> None:
        self.worker.stop()

    def select(self, theme_id: str) -> bool:
        selected = str(theme_id or "").strip()
        changed = self.builder.select(selected)
        projection = self.hub.projection_snapshot("theme_detail")
        payload = (
            projection.get("payload")
            if isinstance(projection, dict)
            and isinstance(projection.get("payload"), dict)
            else None
        )
        ready_for_selection = (
            isinstance(payload, dict)
            and payload.get("status") == "READY"
            and str(payload.get("theme_id") or "") == selected
        )
        feature_version = self.hub.borrow_feature_snapshot()[0]
        if changed or not ready_for_selection:
            # Selection changes and incomplete warmups must rebuild even when the
            # shared Feature version is unchanged. Reset only this detail worker.
            with self.worker._lock:
                self.worker._completed_feature_version = 0
        if feature_version > 0:
            self.worker.submit(feature_version)
        return changed

    def submit(self, feature_version: int) -> None:
        if self.builder.selected():
            self.worker.submit(feature_version)

    def status(self) -> dict[str, Any]:
        status = self.worker.status()
        status.update(
            {
                "projection_mode": "selected_theme_detail_only",
                "selected_theme_id": self.builder.selected() or None,
                "all_theme_detail_generation_allowed": False,
                "same_feature_selection_rebuild_enabled": True,
                "leader_selection_enabled": True,
                "leader_candidate_score_used": False,
            }
        )
        return status
