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
    coordinator._needs = lambda row: (False, False, bool(row["need_order"]), False, False)

    # Bypass the original refresh and exercise the policy wrapper's post-processing.
    original_refresh = AfterCloseRecoveryCoordinator._refresh
    base_refresh = original_refresh.__closure__[0].cell_contents if original_refresh.__closure__ else None
    assert base_refresh is not None
    coordinator.last_refresh_at = 1.0
    coordinator.refresh_sec = 5.0
    base_refresh(coordinator, 2.0)
    original_refresh(coordinator, 2.0)

    tasks = list(coordinator.queue)
    assert len(tasks) == 7
    assert tasks[0] == ("orderbook", "000000")
    assert tasks[-1] == ("orderbook", "000006")
    assert coordinator.missing_orderbook_count == 7
    assert coordinator.orderbook_all_lane_enqueue_count == 7


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
