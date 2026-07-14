from __future__ import annotations

import argparse
import importlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Production critical path. Keep the 32-bit QAx owner as close as possible to
# the long-running price-only collector that was verified on Windows/Kiwoom.
# Do not import provider, auxiliary TR, orderbook, strength, off-hours, or
# large-trade aggregation stacks into this process.
base = importlib.import_module("realtime_v2.collector32")

from realtime_v2.collector_sender_resilience_patch import (
    install as install_collector_sender_resilience,
)
from realtime_v2.collector_sender_ordering_patch import (
    install as install_collector_sender_ordering,
)

install_collector_sender_resilience(base)
install_collector_sender_ordering(base)

_REALTIME_FIDS = "10;12;20;14"
_REALTIME_BATCH_SIZE = 100
_REALTIME_SCREEN_START = 5100
_COLLECTOR_MODE = "minimal_qax_price_only_v2"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class MinimalCollectorStatus:
    def __init__(self, trade_value_sample_interval_ms: int) -> None:
        self.lock = threading.RLock()
        self.running = False
        self.login_state = "not_requested"
        self.login_error_code: Any = None
        self.login_completed_at: str | None = None
        self.realreg_requested = False
        self.realreg_succeeded = False
        self.realreg_error: str | None = None
        self.realreg_code_count = 0
        self.realreg_screen_count = 0
        self.realreg_screens: list[str] = []
        self.realdata_received_count = 0
        self.trade_event_received_count = 0
        self.realdata_last_received_at: str | None = None
        self.trade_event_last_received_at: str | None = None
        self.trade_value_sample_interval_ms = trade_value_sample_interval_ms
        self.trade_value_sample_count = 0
        self.trade_value_sample_skip_count = 0
        self.last_error: str | None = None
        self.openapi_native_handle_ready = False
        self.openapi_native_hwnd: int | None = None
        self.openapi_native_handle_error: str | None = None
        self.started_at = base.now_text()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "available": True,
                "running": self.running,
                "collector_mode": _COLLECTOR_MODE,
                "started_at": self.started_at,
                "login_requested": self.login_state != "not_requested",
                "login_state": self.login_state,
                "login_error_code": self.login_error_code,
                "login_completed_at": self.login_completed_at,
                "realreg_requested": self.realreg_requested,
                "realreg_succeeded": self.realreg_succeeded,
                "realreg_error": self.realreg_error,
                "realreg_code_count": self.realreg_code_count,
                "realreg_screen_count": self.realreg_screen_count,
                "realreg_screens": list(self.realreg_screens),
                "realreg_fids": _REALTIME_FIDS,
                "realdata_received_count": self.realdata_received_count,
                "trade_event_received_count": self.trade_event_received_count,
                "realdata_last_received_at": self.realdata_last_received_at,
                "trade_event_last_received_at": self.trade_event_last_received_at,
                "trade_value_sample_interval_ms": self.trade_value_sample_interval_ms,
                "trade_value_sample_count": self.trade_value_sample_count,
                "trade_value_sample_skip_count": self.trade_value_sample_skip_count,
                "trade_qty_read_count": 0,
                "large_trade_enabled": False,
                "large_trade_input_fid": None,
                "large_trade_policy": "disabled_in_production_price_collector",
                "openapi_native_handle_ready": self.openapi_native_handle_ready,
                "openapi_native_hwnd": self.openapi_native_hwnd,
                "openapi_native_handle_error": self.openapi_native_handle_error,
                "last_error": self.last_error,
            }


def _native_hwnd(app, control, state: MinimalCollectorStatus) -> int:
    try:
        from PyQt5.QtCore import Qt

        control.setAttribute(Qt.WA_NativeWindow, True)
    except Exception:
        pass

    try:
        control.resize(2, 2)
        control.move(-32000, -32000)
        control.show()
        app.processEvents()
        hwnd = int(control.winId())
        app.processEvents()
        if hwnd <= 0:
            raise RuntimeError(f"invalid QAx HWND: {hwnd}")
        if os.name == "nt":
            import ctypes

            if not bool(ctypes.windll.user32.IsWindow(hwnd)):
                raise RuntimeError(f"QAx HWND is not a Windows window: {hwnd}")
        with state.lock:
            state.openapi_native_handle_ready = True
            state.openapi_native_hwnd = hwnd
            state.openapi_native_handle_error = None
        return hwnd
    except Exception as error:
        with state.lock:
            state.openapi_native_handle_ready = False
            state.openapi_native_handle_error = f"{type(error).__name__}: {error}"
            state.last_error = str(error)
        raise


def _registration_batches(codes: list[str]):
    for index in range(0, len(codes), _REALTIME_BATCH_SIZE):
        screen = str(_REALTIME_SCREEN_START + index // _REALTIME_BATCH_SIZE)
        yield screen, codes[index : index + _REALTIME_BATCH_SIZE]


def _real_text(control, received_code: str, fid: int) -> str:
    value = control.dynamicCall(
        "GetCommRealData(QString, int)",
        received_code,
        int(fid),
    )
    return str(value or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="StockBoard v2 price-only minimal QAx collector"
    )
    parser.add_argument("--host", default=base.DEFAULT_HOST)
    parser.add_argument("--event-port", type=int, default=base.DEFAULT_EVENT_PORT)
    parser.add_argument(
        "--codes-file",
        default=str(ROOT / "data" / "runtime" / "stockboard_v2" / "codes.txt"),
    )
    parser.add_argument("--codes", default="")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--suffix", default="AL")
    parser.add_argument("--orderbook", action="store_true")
    parser.add_argument(
        "--flush-ms",
        type=int,
        default=int(os.getenv("STOCKBOARD_V2_COLLECTOR_FLUSH_MS", "50")),
    )
    args = parser.parse_args()

    # --orderbook is retained for launcher compatibility but intentionally ignored.
    codes = base.load_codes(args.codes_file, args.codes, args.limit, args.suffix)
    sender = base.EventSender(args.host, args.event_port, flush_ms=args.flush_ms)
    sender.start()

    trade_value_sample_interval_ms = _env_int(
        "STOCKBOARD_TRADE_VALUE_SAMPLE_MS", 500, 100, 5000
    )
    trade_value_sample_interval_sec = trade_value_sample_interval_ms / 1000.0
    state = MinimalCollectorStatus(trade_value_sample_interval_ms)
    trade_value_last_sample_mono_by_code: dict[str, float] = {}
    trade_value_last_raw_by_code: dict[str, str] = {}

    from PyQt5.QtCore import QCoreApplication, QTimer
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QAxContainer import QAxWidget

    app = QCoreApplication.instance()
    if app is None:
        app = QApplication([])
    elif not isinstance(app, QApplication):
        raise RuntimeError("QAxWidget requires QApplication")
    app.setQuitOnLastWindowClosed(False)

    control = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
    if control.isNull():
        raise RuntimeError("failed to create KHOPENAPI.KHOpenAPICtrl.1")

    hwnd = _native_hwnd(app, control, state)

    print(
        f"collector codes={len(codes)} suffix={args.suffix} "
        f"orderbook=False flush_ms={args.flush_ms}",
        flush=True,
    )
    print(f"collector_mode={_COLLECTOR_MODE}", flush=True)
    print(f"native_hwnd={hwnd}", flush=True)
    print(
        f"trade_value_sample_interval_ms={trade_value_sample_interval_ms} "
        "large_trade_enabled=False",
        flush=True,
    )

    def publish_status() -> None:
        status = state.status()
        base.publish_collector_status(
            sender,
            state,
            {
                "provider_started": bool(status.get("running")),
                "registered_count_requested": len(codes),
                "registered_count": (
                    int(status.get("realreg_code_count") or 0)
                    if status.get("realreg_succeeded") is True
                    else 0
                ),
                "collector_ready": bool(
                    status.get("running")
                    and status.get("login_state") == "connected"
                    and status.get("realreg_succeeded") is True
                    and int(status.get("realreg_code_count") or 0) > 0
                ),
                "collector_mode": _COLLECTOR_MODE,
            },
        )

    def on_event_connect(error_code) -> None:
        now = base.now_text()
        try:
            error_value = int(error_code)
        except (TypeError, ValueError):
            error_value = error_code

        with state.lock:
            state.login_error_code = error_value
            state.login_completed_at = now
            if error_value == 0:
                state.login_state = "connected"
                state.last_error = None
            else:
                state.login_state = "failed"
                state.last_error = f"OnEventConnect failed: {error_value}"

        if error_value != 0:
            publish_status()
            app.exit(1)
            return

        screens: list[str] = []
        try:
            with state.lock:
                state.realreg_requested = True
            for screen, batch in _registration_batches(codes):
                result = control.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    screen,
                    ";".join(batch),
                    _REALTIME_FIDS,
                    "0",
                )
                if result not in (None, 0, "0"):
                    raise RuntimeError(
                        f"SetRealReg screen {screen} returned {result!r}"
                    )
                screens.append(screen)

            with state.lock:
                state.realreg_succeeded = True
                state.realreg_error = None
                state.realreg_code_count = len(codes)
                state.realreg_screen_count = len(screens)
                state.realreg_screens = screens

            print(
                f"collector_ready=True registered_count={len(codes)} "
                f"screens={len(screens)}",
                flush=True,
            )
            publish_status()
        except Exception as error:
            with state.lock:
                state.realreg_succeeded = False
                state.realreg_error = f"{type(error).__name__}: {error}"
                state.last_error = str(error)
            print(
                f"collector_registration_error={type(error).__name__}: {error}",
                flush=True,
            )
            publish_status()
            app.exit(1)

    def on_receive_real_data(received_code, real_type, _real_data) -> None:
        now = base.now_text()
        with state.lock:
            state.realdata_received_count += 1
            state.realdata_last_received_at = now

        if "주식체결" not in str(real_type or ""):
            return

        normalized_code = base.normalize_code(received_code)
        if not normalized_code:
            return

        try:
            price_raw = _real_text(control, str(received_code), 10)
            change_rate_raw = _real_text(control, str(received_code), 12)
            trade_time_raw = _real_text(control, str(received_code), 20)

            sample_now = time.monotonic()
            last_sample = trade_value_last_sample_mono_by_code.get(normalized_code)
            should_sample_value = (
                last_sample is None
                or sample_now - last_sample >= trade_value_sample_interval_sec
                or normalized_code not in trade_value_last_raw_by_code
            )
            if should_sample_value:
                cumulative_value_raw = _real_text(
                    control, str(received_code), 14
                )
                trade_value_last_sample_mono_by_code[normalized_code] = sample_now
                trade_value_last_raw_by_code[normalized_code] = cumulative_value_raw
                with state.lock:
                    state.trade_value_sample_count += 1
            else:
                cumulative_value_raw = trade_value_last_raw_by_code.get(
                    normalized_code, ""
                )
                with state.lock:
                    state.trade_value_sample_skip_count += 1
        except Exception as error:
            with state.lock:
                state.last_error = (
                    f"GetCommRealData failed: {type(error).__name__}: {error}"
                )
            return

        with state.lock:
            state.trade_event_received_count += 1
            state.trade_event_last_received_at = now

        sender.publish_trade(
            {
                "type": "trade",
                "ts": now,
                "stock_code": normalized_code,
                "received_code": str(received_code),
                "kwargs": {
                    "price": price_raw,
                    "change_rate": change_rate_raw,
                    "trade_time": trade_time_raw,
                    "fid20_trade_time": trade_time_raw,
                    "cumulative_value": cumulative_value_raw,
                    "price_received_at": now,
                    "trade_received_at": now,
                    "received_at": now,
                    "source_code": str(received_code),
                    "registered_code": str(received_code),
                    "raw": {
                        "price_raw": price_raw,
                        "change_rate_raw": change_rate_raw,
                        "trade_time_raw": trade_time_raw,
                        "cumulative_value_raw": cumulative_value_raw,
                    },
                },
            }
        )

    control.OnEventConnect.connect(on_event_connect)
    control.OnReceiveRealData.connect(on_receive_real_data)

    heartbeat = QTimer()
    heartbeat.setInterval(1000)
    heartbeat.timeout.connect(publish_status)
    heartbeat.start()

    with state.lock:
        state.running = True
        state.login_state = "requested"

    result = control.dynamicCall("CommConnect()")
    if result not in (None, 0, "0"):
        with state.lock:
            state.login_state = "failed"
            state.login_error_code = result
            state.last_error = f"CommConnect returned {result!r}"
        publish_status()
        sender.stop()
        return 1

    publish_status()
    print(
        "qt_event_loop=exec_ critical_fids=10,12,20 "
        f"sampled_fid14_ms={trade_value_sample_interval_ms}",
        flush=True,
    )

    app_result = 0
    try:
        app_result = int(app.exec_() or 0)
    except KeyboardInterrupt:
        app_result = 0
    finally:
        with state.lock:
            state.running = False
        try:
            heartbeat.stop()
        except Exception:
            pass
        sender.stop()
        if sender.is_alive():
            sender.join(timeout=2.0)

    print(f"collector_app_result={app_result}", flush=True)
    return app_result


if __name__ == "__main__":
    raise SystemExit(main())
