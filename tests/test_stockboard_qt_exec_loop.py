from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


def source_text() -> str:
    return ENTRYPOINT_PATH.read_text(encoding="utf-8")


def test_minimal_qax_collector_is_valid_python():
    ast.parse(source_text())


def test_production_collector_does_not_import_provider_or_heavy_patch_stack():
    source = source_text()
    assert "KiwoomOpenApiRealtimeProvider" not in source
    assert 'import_module("realtime_v2.collector32_large")' not in source
    assert "qt_main_thread_openapi_patch" not in source
    assert "openapi_native_handle_patch" not in source
    assert "offhours_metric" not in source
    assert "strength5m" not in source
    assert "orderbook_thin_scheduler" not in source


def test_qapplication_and_qaxwidget_are_created_directly_on_main_thread():
    source = source_text()
    assert "QApplication([])" in source
    assert 'QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")' in source
    assert "control.OnEventConnect.connect(on_event_connect)" in source
    assert "control.OnReceiveRealData.connect(on_receive_real_data)" in source
    assert "app.exec_()" in source
    assert "minimal_qax_price_only_v2" in source


def test_registration_runs_inside_login_success_callback():
    source = source_text()
    on_connect = source[
        source.index("def on_event_connect"):source.index("def on_receive_real_data")
    ]
    assert 'state.login_state = "connected"' in on_connect
    assert "SetRealReg(QString, QString, QString, QString)" in on_connect
    assert "collector_ready=True" in on_connect


def test_critical_callback_reads_only_price_rate_time_and_sampled_trade_value():
    source = source_text()
    assert '_REALTIME_FIDS = "10;12;20;14"' in source
    callback = source[
        source.index("def on_receive_real_data"):source.index(
            "control.OnEventConnect.connect"
        )
    ]
    for fid in (10, 12, 20):
        assert f", {fid})" in callback
    assert "control, str(received_code), 14\n" in callback
    assert "STOCKBOARD_TRADE_VALUE_SAMPLE_MS" in source
    assert "trade_value_sample_skip_count" in source
    assert "should_sample_value" in callback

    # FID15 and every former auxiliary FID stay out of the production QAx callback.
    for excluded_fid in (15, 13, 228, 41, 51, 121, 125):
        assert f", {excluded_fid})" not in callback
    assert '"trade_qty":' not in callback
    assert '"trade_qty_raw":' not in callback


def test_callback_only_enqueues_latest_trade_and_never_does_socket_io():
    source = source_text()
    callback = source[
        source.index("def on_receive_real_data"):source.index(
            "control.OnEventConnect.connect"
        )
    ]
    assert "sender.publish_trade(" in callback
    assert "sendall" not in callback
    assert "socket" not in callback


def test_transport_patches_remain_but_large_trade_patch_is_not_installed():
    source = source_text()
    assert "collector_sender_resilience_patch" in source
    assert "collector_sender_ordering_patch" in source
    assert "install_collector_sender_resilience(base)" in source
    assert "install_collector_sender_ordering(base)" in source
    assert "collector_large_trade_patch" not in source
    assert "install_collector_large_trade" not in source
    assert '"large_trade_enabled": False' in source
