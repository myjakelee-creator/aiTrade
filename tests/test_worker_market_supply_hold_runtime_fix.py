from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import realtime_v2
from realtime_v2 import worker_market_supply_hold_runtime_fix as runtime_fix
from realtime_v2 import worker_tr_singleflight_patch as tr_patch

ROOT = Path(__file__).resolve().parents[1]


class _FakeHolder:
    def resolve(self, original_market_supply):
        return (
            {"kospi": {"market_index": 1}, "kosdaq": {"market_index": 2}},
            {"display_basis": "after_close_hold"},
        )


def test_runtime_fix_wraps_actual_guarded_context_owner(monkeypatch):
    guarded = ModuleType("realtime_v2.worker64_guarded")
    guarded.base = SimpleNamespace()
    guarded._runtime_context_payload = lambda: {"market_supply": {"invalid": True}}

    monkeypatch.setitem(sys.modules, "realtime_v2.worker64_guarded", guarded)
    monkeypatch.setattr(realtime_v2, "worker64_guarded", guarded, raising=False)
    monkeypatch.setattr(runtime_fix, "MarketSupplyHold", _FakeHolder)

    runtime_fix.install()

    payload = guarded._runtime_context_payload()
    assert payload["market_supply"]["kospi"]["market_index"] == 1
    assert payload["market_supply_status"]["display_basis"] == "after_close_hold"
    assert payload["market_supply_status"]["context_owner"] == (
        "realtime_v2.worker64_guarded"
    )
    assert guarded._market_supply_hold_patch_installed is True


def test_market_supply_install_failure_is_fail_open_and_clears_after_recovery(
    monkeypatch, tmp_path: Path
):
    failing = ModuleType("realtime_v2.worker_market_supply_hold_runtime_fix")

    def fail_install():
        raise RuntimeError("synthetic market hold failure")

    failing.install = fail_install
    monkeypatch.setitem(
        sys.modules, "realtime_v2.worker_market_supply_hold_runtime_fix", failing
    )

    fake_base = SimpleNamespace(RUNTIME_DIR=tmp_path)
    assert tr_patch._install_market_supply_hold_fail_open(fake_base) is False

    error_path = tmp_path / "market_supply_hold_patch_error.txt"
    assert error_path.is_file()
    assert "synthetic market hold failure" in error_path.read_text(
        encoding="utf-8"
    )

    recovered = ModuleType("realtime_v2.worker_market_supply_hold_runtime_fix")
    recovered.install = lambda: None
    monkeypatch.setitem(
        sys.modules, "realtime_v2.worker_market_supply_hold_runtime_fix", recovered
    )

    assert tr_patch._install_market_supply_hold_fail_open(fake_base) is True
    assert not error_path.exists()


def test_production_entrypoint_keeps_mobile_generation_cache_continuity_and_momentum_accuracy_installed():
    script = r'''
import importlib
from pathlib import Path

production = importlib.import_module("realtime_v2.worker64_guarded_large_bidask")
guarded = importlib.import_module("realtime_v2.worker64_guarded")
large = importlib.import_module("realtime_v2.worker64_guarded_large")
board_guard = importlib.import_module("realtime_v2.worker_board_trading_date_guard")
continuity = importlib.import_module("realtime_v2.worker_board_display_continuity_patch")
continuity_safety = importlib.import_module("realtime_v2.worker_board_display_continuity_safety")
opening = importlib.import_module("realtime_v2.worker_opening_burst_cache_patch")
accuracy = importlib.import_module("realtime_v2.worker_momentum_accuracy_patch")
bridge = importlib.import_module("realtime_v2.worker_momentum_accuracy_stage_bridge")

assert production is not None
assert getattr(guarded, "_market_supply_hold_patch_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_portable_board_guard_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_portable_cache_sync_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_opening_burst_cache_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_display_live_tracking_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_display_cache_fallback_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_display_continuity_rlock_installed", False) is True
assert getattr(board_guard.PortableBoardGuard, "_stockboard_display_continuity_installed", False) is True
assert continuity.PATCH_VERSION == "board_display_continuity_v1"
assert continuity_safety.PATCH_VERSION == "board_display_continuity_rlock_v2"
assert getattr(continuity, "_display_continuity_rlock_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_momentum_accuracy_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_momentum_accuracy_stage_bridge_installed", False) is True
assert getattr(guarded.base.State, "_stockboard_momentum_accuracy_stage_owner", None) == bridge.PATCH_VERSION
assert accuracy.PATCH_VERSION == "momentum_accuracy_guard_v1"
assert board_guard.PORTABLE_PARSER_VERSION == "exact_daily_row_fields_v2"
assert getattr(opening, "_portable_generation_signature_installed", False) is True
payload = guarded._runtime_context_payload()
assert "market_supply_status" in payload
html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
patched = large._ui_safety_patch(html)
assert "STOCKBOARD_V2_RESPONSIVE_MOBILE_VIEW_20260717" in patched
assert 'id="stockboard-view-toggle"' in patched
print("production_patch_chain_ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "production_patch_chain_ok" in result.stdout
