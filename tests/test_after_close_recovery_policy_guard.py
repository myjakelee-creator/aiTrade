from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.after_close_recovery_hardening import install_module_hardening
from realtime_v2.after_close_recovery_policy_guard import install as install_policy_guard
from realtime_v2.after_close_recovery_sampler_guard import install_module_guard

install_module_hardening()
install_module_guard()
install_policy_guard()

from realtime_v2.after_close_recovery import (  # noqa: E402
    AfterCloseRecoveryCoordinator,
    CloseWindowSamplerService,
)


def test_sampler_nearest_never_uses_sample_after_target():
    target = datetime(2026, 7, 10, 20, 0, 0)
    after = (target + timedelta(seconds=5), {"000001": 10.0}, {})
    before = (target - timedelta(seconds=2), {"000001": 9.0}, {})

    assert CloseWindowSamplerService._nearest([after], target) is None
    assert CloseWindowSamplerService._nearest([after, before], target) == before


def test_all_p0_to_p6_orderbook_tasks_are_enqueued():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.plan = [
        {
            "stock_code": f"00000{lane}",
            "lane": lane,
            "row": {"need_order": True},
        }
        for lane in range(7)
    ]
    coordinator.queue = deque()
    coordinator.current = None
    coordinator.completed = set()
    coordinator.retry_at = {}
    coordinator.settle_until = {}
    coordinator.mode = "complete"
    coordinator.orderbook_all_lane_enqueue_count = 0
    coordinator.missing_orderbook_count = 0
    coordinator.target_date = lambda: "20260710"
    coordinator._needs = lambda row: (
        False,
        False,
        bool(row["need_order"]),
        False,
        False,
    )

    # Make the wrapped original refresh return on its interval guard. The policy
    # post-processing must still add every missing P0-P6 orderbook task.
    coordinator.last_refresh_at = 1.0
    coordinator.refresh_sec = 5.0
    AfterCloseRecoveryCoordinator._refresh(coordinator, 2.0)

    tasks = list(coordinator.queue)
    assert len(tasks) == 7
    assert tasks[0] == ("orderbook", "000000")
    assert tasks[-1] == ("orderbook", "000006")
    assert coordinator.missing_orderbook_count == 7
    assert coordinator.orderbook_all_lane_enqueue_count == 7


def test_theme_master_codes_outside_current_universe_are_filtered():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.base = SimpleNamespace(
        normalize_code=lambda value: str(value or "")
        if len(str(value or "")) == 6
        else ""
    )
    coordinator.scheduler_module = SimpleNamespace(
        _load_selected=lambda _base: "000001",
        _model_rank=lambda row, fallback: int(row.get("model_rank") or fallback),
    )
    coordinator.theme_members = {
        "T1": (("000002", 0.5), ("999999", 0.5)),
    }
    coordinator._theme_data = lambda: {
        "themes": [{"theme_id": "T1", "leaders": []}]
    }
    rows = [
        {"stock_code": "000001", "model_rank": 1},
        {"stock_code": "000002", "model_rank": 2},
    ]

    plan = coordinator._build_plan({"rows": rows})
    codes = [item["stock_code"] for item in plan]
    assert codes == ["000001", "000002"]
    assert coordinator.outside_universe_filtered_count == 1


def test_runtime_installs_policy_guard_in_collector_and_worker():
    root = Path(__file__).resolve().parents[1]
    collector = (root / "realtime_v2" / "collector32_large_bidask.py").read_text(
        encoding="utf-8"
    )
    platform = (root / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "install_policy_guard()" in collector
    assert "install_policy_guard()" in platform
