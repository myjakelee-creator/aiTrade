from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from realtime_v2.after_close_recovery_hardening import install_module_hardening
from realtime_v2.after_close_recovery_sampler_guard import (
    _metadata_current_exact,
    install_module_guard,
)

install_module_hardening()
install_module_guard()

from realtime_v2.after_close_recovery import AfterCloseRecoveryCoordinator  # noqa: E402


def exact_row(trading_date="20260710"):
    return {
        "minute_recovery_status": "",
        "source_metadata": {
            "trade_value_1m_eok": {
                "value": 12.0,
                "source": "CLOSE_SAMPLER_EXACT",
                "trading_date": trading_date,
                "is_estimated": False,
            },
            "trade_value_5m_eok": {
                "value": 40.0,
                "source": "CLOSE_SAMPLER_EXACT",
                "trading_date": trading_date,
                "is_estimated": False,
            },
        },
    }


def test_exact_current_sampler_values_skip_minute_fallback():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.target_date = lambda: "20260710"
    coordinator.delegate = SimpleNamespace(
        _row_needs=lambda _coordinator, _row: (False, False, False)
    )
    result = coordinator._needs(exact_row())
    assert result[0] is False


def test_prior_day_or_estimated_sampler_metadata_does_not_skip_fallback():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.target_date = lambda: "20260713"
    coordinator.delegate = SimpleNamespace(
        _row_needs=lambda _coordinator, _row: (False, False, False)
    )
    assert coordinator._needs(exact_row("20260710"))[0] is True
    row = exact_row("20260713")
    row["source_metadata"]["trade_value_5m_eok"]["is_estimated"] = True
    assert coordinator._needs(row)[0] is True


def test_exact_metadata_requires_both_one_and_five_minute_values():
    row = exact_row()
    assert _metadata_current_exact(row, "20260710") is True
    del row["source_metadata"]["trade_value_1m_eok"]
    assert _metadata_current_exact(row, "20260710") is False


def test_runtime_installs_sampler_guard_on_both_processes():
    root = Path(__file__).resolve().parents[1]
    collector = (root / "realtime_v2" / "collector32_large_bidask.py").read_text(
        encoding="utf-8"
    )
    platform = (root / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "install_module_guard()" in collector
    assert "install_worker_guard(base)" in platform
