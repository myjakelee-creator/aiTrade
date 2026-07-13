from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"
PATCH_PATH = ROOT / "realtime_v2" / "qt_main_thread_openapi_patch.py"


def entrypoint_text() -> str:
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


def test_main_thread_light_collector_is_valid_python():
    ast.parse(entrypoint_text())
    ast.parse(PATCH_PATH.read_text(encoding="utf-8"))


def test_production_uses_only_provider_side_main_thread_constructor():
    source = entrypoint_text()
    assert "from realtime_v2.qt_main_thread_openapi_patch import install_provider" in source
    assert "install_provider()" in source
    assert "install_collector_main" not in source
    assert "pump_inline_qt_once" not in source


def test_qax_is_created_on_main_thread_and_uses_real_qt_event_loop():
    source = entrypoint_text()
    assert "install_native_handle()" in source
    assert "provider.start_inline_qt()" in source
    assert "app.exec_()" in source
    assert "collector_mode=main_thread_light" in source
    assert "WARNING: QApplication was not created in the main() thread" not in source


def test_registration_is_deferred_until_login_connected():
    source = entrypoint_text()
    assert 'login_state == "connected"' in source
    assert "provider._process_pending_realtime_requests()" in source
    assert "registered_count_requested" in source
    assert "collector_ready" in source


def test_light_timer_does_not_run_heavy_provider_queues():
    source = entrypoint_text()
    assert 'STOCKBOARD_QT_STARTUP_TIMER_MS", "100"' in source
    assert 'STOCKBOARD_QT_STEADY_TIMER_MS", "1000"' in source
    for call in (
        "provider._process_strength_probe_queue()",
        "provider._process_orderbook_probe_queue()",
        "provider._process_opt10055_probe_queue()",
        "provider._process_close_metrics_queue()",
    ):
        assert call not in source


def test_failure_patch_stack_is_not_installed_in_production():
    source = entrypoint_text()
    for module_name in (
        "strength5m_snapshot_fallback_patch",
        "strength5m_definitive_preopen_patch",
        "offhours_metric_completion_patch",
        "offhours_metric_resilience_patch",
        "offhours_metric_timer_driver_patch",
        "collector_readiness_gate_patch",
    ):
        assert module_name not in source


def test_transport_repairs_and_optional_orderbook_are_fail_open():
    source = entrypoint_text()
    assert "collector_sender_resilience_patch" in source
    assert "collector_sender_ordering_patch" in source
    assert "orderbook_thin_scheduler" in source
    assert "_install_orderbook_thin_fail_open()" in source
    assert "bidask_collector_patch_error.txt" in source
