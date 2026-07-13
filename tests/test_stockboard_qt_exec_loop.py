from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"
PATCH_PATH = ROOT / "realtime_v2" / "qt_main_thread_openapi_patch.py"


def entrypoint_text() -> str:
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


def test_restored_collector_entrypoint_is_valid_python():
    ast.parse(entrypoint_text())


def test_experimental_qt_patch_remains_valid_but_is_not_installed_in_production():
    ast.parse(PATCH_PATH.read_text(encoding="utf-8"))
    source = entrypoint_text()
    assert "qt_main_thread_openapi_patch" not in source
    assert "install_collector_main" not in source
    assert "install_provider" not in source


def test_production_entrypoint_uses_verified_provider_owned_qt_pump():
    source = entrypoint_text()
    assert 'importlib.import_module("realtime_v2.collector32_large")' in source
    assert "raise SystemExit(base.main())" in source


def test_production_entrypoint_excludes_the_failure_patch_stack():
    source = entrypoint_text()
    for module_name in (
        "openapi_native_handle_patch",
        "strength5m_snapshot_fallback_patch",
        "strength5m_definitive_preopen_patch",
        "offhours_metric_completion_patch",
        "offhours_metric_resilience_patch",
        "offhours_metric_timer_driver_patch",
        "collector_sender_resilience_patch",
        "collector_readiness_gate_patch",
    ):
        assert module_name not in source


def test_optional_thin_orderbook_patch_stays_fail_open():
    source = entrypoint_text()
    assert "orderbook_thin_scheduler" in source
    assert "_install_orderbook_thin_fail_open()" in source
    assert "bidask_collector_patch_error.txt" in source
