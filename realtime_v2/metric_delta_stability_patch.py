from __future__ import annotations

"""Stabilize metric SSE delta fingerprints for non-finite scalar values.

Some optional live metrics can temporarily carry NaN/Infinity sentinels.  Python
compares NaN unequal to itself, so the original fingerprint marked an unchanged row
as changed on every 500 ms pass.  That turned a delta stream into a 300-row browser
re-render loop and could starve the independent price fast-patch.

This patch normalizes non-finite numeric values to stable tokens before the metric
transport is installed.  It does not alter metric values in State.quotes or in the
outbound payload; it only changes change-detection fingerprints.
"""

import math
from typing import Any

PATCH_VERSION = "metric_delta_stability_v1"


def _stable_value(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value):
            return ("nonfinite", "nan")
        if math.isinf(value):
            return ("nonfinite", "inf" if value > 0 else "-inf")
        return value
    if isinstance(value, dict):
        return tuple(sorted((str(key), _stable_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_stable_value(item) for item in value)
    return value


def install_runtime_wrapper() -> None:
    from realtime_v2 import metric_fast_sse_patch as target

    if getattr(target, "_metric_delta_stability_wrapped", False):
        return

    fields = ("rank",) + tuple(target._METRIC_FIELDS)

    def stable_fingerprint(row: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(_stable_value(row.get(key)) for key in fields)

    original_install = target.install

    def install_with_stability(base, large=None) -> None:
        target._fingerprint = stable_fingerprint
        target.PATCH_VERSION = "metric_fast_sse_v2"
        original_install(base, large)
        state_class = getattr(base, "State", None)
        if state_class is not None:
            state_class._stockboard_metric_delta_stability_version = PATCH_VERSION

    target._fingerprint = stable_fingerprint
    target.PATCH_VERSION = "metric_fast_sse_v2"
    target.install = install_with_stability
    target._metric_delta_stability_wrapped = True


__all__ = ["PATCH_VERSION", "install_runtime_wrapper"]
