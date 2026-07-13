from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "realtime_v2" / "qt_main_thread_openapi_patch.py"


def source_text() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def test_qt_collector_patch_is_valid_python():
    ast.parse(source_text())


def test_collector_uses_real_qt_event_loop():
    source = source_text()
    assert "app.exec_()" in source
    assert "qt_event_loop=exec_" in source
    assert "pump_timer.timeout.connect(collector_tick)" in source
    assert "pump_timer.start()" in source


def test_exec_loop_does_not_reenter_process_events():
    source = source_text()
    assert "if not exec_loop_active:\n                app.processEvents()" in source
    assert "_stockboard_qt_exec_loop_active = True" in source
    assert "app.setQuitOnLastWindowClosed(False)" in source


def test_provider_pending_work_stays_on_qt_owner_timer():
    source = source_text()
    for call in (
        "self._process_pending_realtime_requests()",
        "self._process_orderbook_rotation()",
        "self._process_strength_probe_queue()",
        "self._process_orderbook_probe_queue()",
        "self._process_opt10055_probe_queue()",
        "self._process_close_metrics_queue()",
    ):
        assert call in source
    assert 'int(os.getenv("STOCKBOARD_QT_PUMP_TIMER_MS", "20"))' in source


def test_exec_loop_health_is_exposed_in_provider_status():
    source = source_text()
    for field in (
        '"qt_exec_loop_active"',
        '"qt_exec_loop_started_at"',
        '"qt_exec_loop_tick_count"',
        '"qt_exec_loop_last_tick_at"',
    ):
        assert field in source


def test_old_manual_sleep_loop_is_removed_from_collector_main():
    source = source_text()
    start = source.index("def install_collector_main")
    collector_main = source[start:]
    assert "while True:" not in collector_main
    assert "time.sleep(0.02)" not in collector_main
