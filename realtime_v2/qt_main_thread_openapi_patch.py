from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import datetime
from threading import Event


def install_provider() -> None:
    """Add a main-thread Qt mode to the existing Kiwoom provider."""

    import kiwoom_data_provider as provider_module

    provider_class = provider_module.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_qt_main_thread_installed", False):
        return

    original_stop = provider_class.stop
    original_status = provider_class.status

    def _inline_cleanup(self) -> bool:
        with self._lock:
            stop_event = self._qt_pump_stop_event
            control = self._control
            app = self._app
            owns_app = bool(self._owns_app)
        if stop_event is not None:
            stop_event.set()

        cleanup_error = None
        try:
            if control is not None:
                control.clear()
                control.deleteLater()
            if app is not None:
                app.processEvents()
                if owns_app:
                    app.quit()
        except Exception as error:
            cleanup_error = error

        with self._lock:
            if cleanup_error is not None:
                self._last_error = f"QAxWidget inline cleanup failed: {cleanup_error}"
            self._qt_pump_running = False
            self._control = None
            self._app = None
            self._qt_thread = None
            self._owns_app = False
            self._qt_ready = False
            self._control_created = False
            self._running = False
            self._qt_pump_thread = None
            self._qt_pump_stop_event = None
            self._qt_ready_event = None
            self._stockboard_inline_qt_mode = False
            self._stockboard_inline_qt_owner_ident = None
            self._stockboard_inline_qt_owner_name = None
        return cleanup_error is None

    def start_inline_qt(self) -> bool:
        """Create QApplication and QAxWidget in the collector process main thread."""

        with self._lock:
            if self._running:
                return True
            if not self._check_availability():
                self._running = False
                return False
            self._qt_pump_stop_event = Event()
            self._qt_ready_event = Event()
            self._qt_pump_thread = None
            self._stockboard_inline_qt_mode = True
            self._stockboard_inline_qt_owner_ident = threading.get_ident()
            self._stockboard_inline_qt_owner_name = threading.current_thread().name
            ready_event = self._qt_ready_event

        app = None
        control = None
        owns_app = False
        try:
            from PyQt5.QtCore import QCoreApplication, QThread
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QAxContainer import QAxWidget

            app = QCoreApplication.instance()
            if app is None:
                app = QApplication([])
                owns_app = True
            elif not isinstance(app, QApplication):
                raise RuntimeError(
                    "QAxWidget requires QApplication, but a QCoreApplication "
                    "instance already exists"
                )

            with self._lock:
                self._app = app
                self._qt_thread = QThread.currentThread()
                self._owns_app = owns_app
                self._qt_ready = True
                self._qt_pump_running = True
                self._qt_pump_last_at = datetime.now().isoformat(timespec="seconds")

            control = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
            if control.isNull():
                raise RuntimeError(
                    "failed to create KHOPENAPI.KHOpenAPICtrl.1 QAx control"
                )
            control.OnEventConnect.connect(self._on_event_connect)
            control.OnReceiveRealData.connect(self._on_receive_real_data)
            control.OnReceiveTrData.connect(self._on_receive_tr_data)

            with self._lock:
                self._control = control
                self._control_created = True
                self._tr_event_connected = True
                self._running = True

            self._request_login()
            if ready_event is not None:
                ready_event.set()
            with self._lock:
                return self._running
        except Exception as error:
            with self._lock:
                self._last_error = f"QAxWidget inline initialization failed: {error}"
                self._running = False
                self._qt_ready = False
                self._control_created = False
                if ready_event is not None:
                    ready_event.set()
            _inline_cleanup(self)
            return False

    def pump_inline_qt_once(self) -> bool:
        """Process Qt/OpenAPI events once from the same thread that owns QAxWidget."""

        with self._lock:
            inline_mode = bool(getattr(self, "_stockboard_inline_qt_mode", False))
            owner_ident = getattr(self, "_stockboard_inline_qt_owner_ident", None)
            app = self._app
            stop_event = self._qt_pump_stop_event
            running = self._running

        if not inline_mode:
            return False
        if owner_ident != threading.get_ident():
            with self._lock:
                self._last_error = "inline Qt pump called from a non-owner thread"
                self._qt_pump_running = False
            return False
        if not running or app is None or stop_event is None or stop_event.is_set():
            return False

        try:
            app.processEvents()
            self._process_pending_realtime_requests()
            self._process_orderbook_rotation()
            self._process_strength_probe_queue()
            self._process_orderbook_probe_queue()
            self._process_opt10055_probe_queue()
            self._process_close_metrics_queue()
            pump_time = datetime.now().isoformat(timespec="seconds")
            with self._lock:
                self._qt_pump_running = True
                self._qt_pump_last_at = pump_time
            return True
        except Exception as error:
            with self._lock:
                self._last_error = f"inline Qt event pump failed: {error}"
                self._qt_pump_running = False
            return False

    def stop(self):
        if getattr(self, "_stockboard_inline_qt_mode", False):
            return _inline_cleanup(self)
        return original_stop(self)

    def status(self):
        result = original_status(self)
        result = result if isinstance(result, dict) else {"status": result}
        result.update(
            {
                "qt_inline_mode": bool(
                    getattr(self, "_stockboard_inline_qt_mode", False)
                ),
                "qt_owner_thread_name": getattr(
                    self, "_stockboard_inline_qt_owner_name", None
                ),
                "qt_owner_is_current_thread": (
                    getattr(self, "_stockboard_inline_qt_owner_ident", None)
                    == threading.get_ident()
                ),
            }
        )
        return result

    provider_class.start_inline_qt = start_inline_qt
    provider_class.pump_inline_qt_once = pump_inline_qt_once
    provider_class.stop = stop
    provider_class.status = status
    provider_class._stockboard_qt_main_thread_installed = True


def install_collector_main(base) -> None:
    """Replace collector32.main with a Qt-main-thread event loop."""

    if getattr(base, "_stockboard_qt_main_thread_main_installed", False):
        return

    def main() -> int:
        parser = argparse.ArgumentParser(
            description="StockBoard v2 32-bit collector adapter"
        )
        parser.add_argument("--host", default=base.DEFAULT_HOST)
        parser.add_argument("--event-port", type=int, default=base.DEFAULT_EVENT_PORT)
        parser.add_argument(
            "--codes-file",
            default=str(
                base.ROOT / "data" / "runtime" / "stockboard_v2" / "codes.txt"
            ),
        )
        parser.add_argument("--codes", default="")
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--suffix", default="AL")
        parser.add_argument("--orderbook", action="store_true")
        parser.add_argument(
            "--flush-ms",
            type=int,
            default=int(os.getenv("STOCKBOARD_V2_COLLECTOR_FLUSH_MS", "50")),
        )
        args = parser.parse_args()

        if args.orderbook:
            os.environ.setdefault("STOCKBOARD_ENABLE_ORDERBOOK_REALTIME", "1")
            os.environ.setdefault("STOCKBOARD_ORDERBOOK_MODE", "hybrid")
            os.environ.setdefault("STOCKBOARD_ORDERBOOK_HOT_SOURCE", "top5")
            os.environ.setdefault("STOCKBOARD_ORDERBOOK_HOT_LIMIT", "5")
            os.environ.setdefault("STOCKBOARD_ORDERBOOK_ROTATE_BATCH", "20")
            os.environ.setdefault("STOCKBOARD_ORDERBOOK_ROTATE_INTERVAL_SEC", "5")
        os.environ.setdefault("STOCKBOARD_PRICE_FAST_MODE", "1")
        os.environ.setdefault(
            "STOCKBOARD_REALTIME_CODE_LIMIT",
            str(max(1, int(args.limit or 300))),
        )

        sender = base.EventSender(args.host, args.event_port, flush_ms=args.flush_ms)
        sender.start()
        store = base.PublishingStore(sender)
        provider = base.KiwoomOpenApiRealtimeProvider(store=store)
        codes = base.load_codes(args.codes_file, args.codes, args.limit, args.suffix)
        print(
            f"collector codes={len(codes)} suffix={args.suffix} "
            f"orderbook={args.orderbook} flush_ms={args.flush_ms}",
            flush=True,
        )

        started = provider.start_inline_qt()
        print(f"provider_start={started}", flush=True)
        if not started:
            base.publish_collector_status(
                sender, provider, {"provider_started": False}
            )
            print(f"provider_status={provider.status()}", flush=True)
            time.sleep(0.2)
            sender.stop()
            return 1

        registered_count = provider.register_codes(codes)
        print(f"registered_count={registered_count}", flush=True)

        last_status_at = 0.0
        console_hidden = False
        try:
            while True:
                if not provider.pump_inline_qt_once():
                    with provider._lock:
                        running = bool(provider._running)
                        last_error = provider._last_error
                    if not running:
                        raise RuntimeError(last_error or "inline Qt provider stopped")

                if not console_hidden:
                    with provider._lock:
                        login_state = provider._login_state
                    if login_state == "connected":
                        console_hidden = base.hide_console_after_login_if_requested()
                        if console_hidden:
                            print(
                                "collector_console_hidden_after_login=True",
                                flush=True,
                            )

                now_mono = time.monotonic()
                if now_mono - last_status_at >= 1.0:
                    base.publish_collector_status(
                        sender,
                        provider,
                        {
                            "provider_started": True,
                            "registered_count": registered_count,
                        },
                    )
                    last_status_at = now_mono
                time.sleep(0.02)
        except KeyboardInterrupt:
            return 0
        finally:
            sender.stop()
            try:
                provider.stop()
            except Exception:
                pass
            if sender.is_alive():
                sender.join(timeout=2.0)

    base.main = main
    base._stockboard_qt_main_thread_main_installed = True
