from __future__ import annotations

import functools
from typing import Any

from . import model_lane


def _strip_model_fields_in_place(row: dict[str, Any]) -> dict[str, Any]:
    for key in tuple(row):
        if key in model_lane._MODEL_FIELDS or key.startswith(model_lane._MODEL_PREFIXES):
            row.pop(key, None)
    return row


def install() -> None:
    """Replace the cached model merge with an in-place implementation.

    Rows reaching the model lane are already detached copies of worker quotes, so
    copying every full row again is unnecessary. Cached model overlays remain
    immutable after publication and are only attached to the detached rows.
    """

    service_class = model_lane.StockBoardModelLaneService
    if getattr(service_class, "_stockboard_model_merge_in_place_installed", False):
        return

    original_status = service_class.status

    def optimized_apply(
        self,
        rows: list[dict[str, Any]],
        model_id: str | None,
    ) -> list[dict[str, Any]]:
        request_model_id = str(model_id or "").strip()
        with self.lock:
            usable = bool(self.result_overlays) and (
                request_model_id == self.result_request_model_id
            )
            overlays = self.result_overlays
            order = self.result_order
            result_model_id = self.result_model_id
            if usable:
                self.reuse_count += 1
            else:
                self.pending_return_count += 1

        if not usable:
            for row in rows:
                _strip_model_fields_in_place(row)
                row.update(
                    {
                        "candidate_model_id": request_model_id or None,
                        "candidate_score": None,
                        "grade_score": None,
                        "candidate_status": "MODEL_PENDING",
                        "model_validation_status": "PENDING",
                        "is_candidate": False,
                        "desired_top20": False,
                    }
                )
            return rows

        fallback_rank = len(order) + 1
        for index, row in enumerate(rows, start=1):
            _strip_model_fields_in_place(row)
            code = model_lane._code(row)
            overlay = overlays.get(code)
            if overlay:
                row.update(overlay)
            row["candidate_model_id"] = row.get("candidate_model_id") or result_model_id
            row["model_lane_reused"] = True
            row["_model_lane_sort"] = order.get(code, fallback_rank + index)

        rows.sort(
            key=lambda row: (
                int(row.pop("_model_lane_sort", fallback_rank)),
                int(row.get("rank") or 10**9),
                model_lane._code(row),
            )
        )
        return rows

    @functools.wraps(original_status)
    def optimized_status(self):
        result = original_status(self)
        result["merge_in_place"] = True
        return result

    service_class.apply = optimized_apply
    service_class.status = optimized_status
    service_class._stockboard_model_merge_in_place_installed = True
