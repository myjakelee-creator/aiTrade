from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

from realtime_v2.common import event_age_sec, normalize_code, now_text


class StaleProjectionInput(RuntimeError):
    """Raised when a projection is built from an obsolete feature snapshot."""


class BoardDataHub:
    """Single read model for StockBoard, ThemeBoard and StrategyBoard.

    The live State remains the canonical owner.  This hub publishes immutable
    completed feature/candidate snapshots and future board projections without
    triggering TR requests or recalculation from HTTP handlers.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state_version = 0
        self._feature_version = 0
        self._candidate_version = 0
        self._last_state_event_at: str | None = None
        self._last_state_event_type: str | None = None
        self._last_state_code: str | None = None
        self._feature_payload_meta: dict[str, Any] = {}
        self._feature_rows: list[dict[str, Any]] = []
        self._feature_published_at: str | None = None
        self._feature_input_state_version = 0
        self._projections: dict[str, dict[str, Any]] = {}
        self._projection_versions: dict[str, int] = {}

    def mark_state_change(
        self,
        *,
        event_type: str,
        at: str | None = None,
        stock_code: Any = None,
    ) -> int:
        with self._lock:
            self._state_version += 1
            self._last_state_event_at = at or now_text()
            self._last_state_event_type = str(event_type or "unknown")
            self._last_state_code = normalize_code(stock_code) or None
            return self._state_version

    def publish_feature_snapshot(self, payload: dict[str, Any]) -> int:
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("feature snapshot requires a rows list")

        meta = dict(payload)
        meta.pop("rows", None)
        meta.pop("row_count", None)
        with self._lock:
            self._feature_version += 1
            self._candidate_version += 1
            self._feature_payload_meta = meta
            # The background snapshot is complete and no longer mutated. Keep the
            # reference here and deepcopy only on consumer reads to avoid another
            # full-universe copy during the expensive build lane.
            self._feature_rows = rows
            self._feature_published_at = now_text()
            self._feature_input_state_version = self._state_version
            return self._feature_version

    def publish_projection(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        input_feature_version: int,
    ) -> int:
        projection_name = str(name or "").strip().lower()
        if not projection_name:
            raise ValueError("projection name is required")
        with self._lock:
            if int(input_feature_version) != self._feature_version:
                raise StaleProjectionInput(
                    f"{projection_name} input feature version {input_feature_version} "
                    f"!= current {self._feature_version}"
                )
            version = int(self._projection_versions.get(projection_name) or 0) + 1
            self._projection_versions[projection_name] = version
            self._projections[projection_name] = {
                "schema_version": 1,
                "source": f"board_data_hub_{projection_name}",
                "projection": projection_name,
                "projection_version": version,
                "input_feature_version": self._feature_version,
                "published_at": now_text(),
                "payload": deepcopy(payload),
            }
            return version

    def manifest(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "source": "board_data_hub",
                "ts": now_text(),
                "state_version": self._state_version,
                "feature_version": self._feature_version,
                "candidate_version": self._candidate_version,
                "feature_input_state_version": self._feature_input_state_version,
                "feature_published_at": self._feature_published_at,
                "feature_row_count": len(self._feature_rows),
                "last_state_event_at": self._last_state_event_at,
                "last_state_event_type": self._last_state_event_type,
                "last_state_code": self._last_state_code,
                "projection_versions": dict(self._projection_versions),
                "policy": {
                    "canonical_state_owner": "stockboard_v2_worker",
                    "realtime_owner": "stockboard_v2_collector32",
                    "direct_board_tr_allowed": False,
                    "direct_board_feature_calculation_allowed": False,
                    "html_calculation_allowed": False,
                    "projection_input": "shared_feature_snapshot_only",
                },
            }

    def feature_snapshot(self, limit: int = 300) -> dict[str, Any]:
        requested_limit = max(1, int(limit or 300))
        with self._lock:
            meta = deepcopy(self._feature_payload_meta)
            rows = [deepcopy(row) for row in self._feature_rows[:requested_limit]]
            feature_version = self._feature_version
            candidate_version = self._candidate_version
            input_state_version = self._feature_input_state_version
            published_at = self._feature_published_at
        meta.update(
            {
                "schema_version": 1,
                "source": "board_data_hub_feature_snapshot",
                "ts": now_text(),
                "feature_version": feature_version,
                "candidate_version": candidate_version,
                "input_state_version": input_state_version,
                "published_at": published_at,
                "row_count": len(rows),
                "rows": rows,
            }
        )
        return meta

    def canonical_snapshot(self, state, limit: int = 300) -> dict[str, Any]:
        requested_limit = max(1, int(limit or 300))
        with state.lock:
            rows = [deepcopy(row) for row in state.quotes.values()]
            status = deepcopy(state.status)
        rows.sort(
            key=lambda row: (
                -(float(row.get("trade_value_eok") or 0.0)),
                int(row.get("seed_rank") or 999999),
                str(row.get("stock_code") or ""),
            )
        )
        rows = rows[:requested_limit]
        for row in rows:
            received_at = row.get("received_at")
            if received_at:
                row["price_age_sec"] = event_age_sec(received_at)
        with self._lock:
            state_version = self._state_version
            feature_version = self._feature_version
        return {
            "schema_version": 1,
            "source": "board_data_hub_canonical_state",
            "ts": now_text(),
            "state_version": state_version,
            "feature_version": feature_version,
            "status": status,
            "row_count": len(rows),
            "rows": rows,
        }

    def projection_snapshot(self, name: str) -> dict[str, Any] | None:
        projection_name = str(name or "").strip().lower()
        with self._lock:
            payload = self._projections.get(projection_name)
            return deepcopy(payload) if payload is not None else None
