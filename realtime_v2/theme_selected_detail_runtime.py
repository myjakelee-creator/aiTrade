from __future__ import annotations

import threading
from typing import Any

from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.board_projection_runtime import LatestOnlyProjectionWorker


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
        changed = self.builder.select(theme_id)
        feature_version = self.hub.borrow_feature_snapshot()[0]
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
            }
        )
        return status
