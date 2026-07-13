from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.market_session_config_cache_patch import install as install_calendar_cache
from realtime_v2.theme_projection_core_timing_patch import install as install_core_timing


def test_market_calendar_config_is_reparsed_only_when_signature_changes(tmp_path: Path):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"value": 1}), encoding="utf-8")
    calls = {"count": 0}

    def original_load():
        calls["count"] += 1
        return json.loads(path.read_text(encoding="utf-8"))

    module = SimpleNamespace(CONFIG_PATH=path, _load_config=original_load)
    install_calendar_cache(module)

    assert module._load_config()["value"] == 1
    assert module._load_config()["value"] == 1
    assert calls["count"] == 1

    # Different size guarantees a different signature even on coarse filesystems.
    path.write_text(json.dumps({"value": 200}), encoding="utf-8")
    assert module._load_config()["value"] == 200
    assert calls["count"] == 2


class DummyBuilder:
    def __call__(self, _feature_version, _rows, _meta):
        time.sleep(0.002)
        return {
            "status": "READY",
            "calculate_ms": 2.0,
            "performance_breakdown": {
                "aggregate_ms": 0.5,
                "score_sort_ms": 0.25,
            },
        }


def test_core_timing_preserves_inner_summary_cost_before_outer_wrappers():
    module = SimpleNamespace(ThemeProjectionBuilder=DummyBuilder)
    install_core_timing(module)

    payload = module.ThemeProjectionBuilder()(1, tuple(), {})
    performance = payload["performance_breakdown"]

    assert performance["summary_core_ms"] >= 1.5
    assert performance["summary_core_other_ms"] >= 0.0
    assert (
        performance["summary_core_timing_policy"]
        == "inner_summary_before_flow_momentum_rank_wrappers"
    )
