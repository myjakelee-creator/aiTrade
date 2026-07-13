from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# QAx/QApplication must be created on the collector process main thread. Reuse
# only the provider-side main-thread constructor; do not install the previous
# 20 ms production pump, off-hours timers, or auxiliary TR patch stack.
from realtime_v2.qt_main_thread_openapi_patch import install_provider
from realtime_v2.openapi_native_handle_patch import install as install_native_handle

install_provider()
install_native_handle()

large = importlib.import_module("realtime_v2.collector32_large")
base = large.base

# Transport repairs do not own QAx or Qt. Keep sender recovery and monotonic
# reconnect ordering while the collector lifecycle is simplified.
from realtime_v2.collector_sender_resilience_patch import (
    install as install_collector_sender_resilience,
)
from realtime_v2.collector_sender_ordering_patch import (
    install as install_collector_sender_ordering,
)

install_collector_sender_resilience(base)
install_collector_sender_ordering(base)


def _runtime_dir() -> Path:
    try:
        return Path(base.RUNTIME_DIR)
    except Exception:
        return ROOT / "data" / "runtime" / "stockboard_v2"


def _install_orderbook_thin_fail_open() -> None:
    try:
        from realtime_v2.orderbook_thin_scheduler import (
            install as install_orderbook_thin_scheduler,
        )

        install_orderbook_thin_scheduler(base)
    except Exception as error:
        try:
            runtime = _runtime_dir()
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "bidask_collector_patch_error.txt").write_text(
                f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
                encoding="utf-8",
            )
        except Exception:
            pass


_install_orderbook_thin_fail_open()


def _provider_status(provider) -> dict:
    try:
        status = provider.status()
        return status if isinstance(status, dict) else {"status": status}
    except Exception as error:
        return {"last_error": f"status failed: {type(error).__name__}: {error}"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="StockBoard v2 main-thread light OpenAPI collector"
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
    else:
        os.environ.setdefault("STOCKBOARD_ENABLE_ORDERBOOK_REALTIME", "0")
        os.environ.setdefault("STOCKBOARD_ORDERBOOK_MODE", "off")

    os.environ.setdefault("STOCKBOARD_PRICE_FAST_MODE", "1")
    os.environ.setdefault(
        "STOCKBOARD_REALTIME_CODE_LIMIT", str(max(1, int(args.limit or 300)))
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
    print("collector_mode=main_thread_light", flush=True)

    started = provider.start_inline_qt()
    print(f"provider_start={started}", flush=True)
    if not started:
        base.publish_collector_status(
            sender,
            provider,
            {
                "provider_started": False,
                "collector_mode": "main_thread_light",
            },
        )
        print(f"provider_status={_provider_status(provider)}", flush=True)
        sender.stop()
        return 1

    requested_count = provider.register_codes(codes)
    print(f"registered_count_requested={requested_count}", flush=True)

    with provider._lock:
        app = provider._app
    if app is None:
        print("collector_qt_event_loop_error=QApplication unavailable", flush=True)
        sender.stop()
        provider.stop()
        return 1

    from PyQt5.QtCore import QTimer

    app.setQuitOnLastWindowClosed(False)
    timer = QTimer()
    startup_interval_ms = max(
        50,
        min(1000, int(os.getenv("STOCKBOARD_QT_STARTUP_TIMER_MS", "100"))),
    )
    steady_interval_ms = max(
        250,
        min(5000, int(os.getenv("STOCKBOARD_QT_STEADY_TIMER_MS", "1000"))),
    )
    timer.setInterval(startup_interval_ms)

    state = {
        "last_status_at": 0.0,
        "console_hidden": False,
        "steady": False,
        "exit_code": 0,
        "exit_error": None,
    }

    def request_exit(code: int, error=None) -> None:
        if code and not state["exit_code"]:
            state["exit_code"] = int(code)
        if error is not None and state["exit_error"] is None:
            state["exit_error"] = str(error)
        try:
            app.exit(int(state["exit_code"] or 0))
        except Exception:
            pass

    def collector_tick() -> None:
        status = _provider_status(provider)
        login_state = str(status.get("login_state") or "")
        running = bool(status.get("running"))

        if login_state == "connected" and status.get("realreg_succeeded") is not True:
            try:
                # This is the only startup provider work performed by the timer.
                # It runs after OnEventConnect and executes the queued SetRealReg.
                provider._process_pending_realtime_requests()
                status = _provider_status(provider)
            except Exception as error:
                print(
                    f"collector_registration_error={type(error).__name__}: {error}",
                    flush=True,
                )
                request_exit(1, error)
                return

        realreg_succeeded = status.get("realreg_succeeded") is True
        actual_count = int(status.get("realreg_code_count") or 0)

        if realreg_succeeded and actual_count > 0 and not state["steady"]:
            state["steady"] = True
            timer.setInterval(steady_interval_ms)
            print(
                f"collector_ready=True registered_count={actual_count} "
                f"steady_timer_ms={steady_interval_ms}",
                flush=True,
            )

        if args.orderbook and realreg_succeeded:
            try:
                provider._process_orderbook_rotation()
            except Exception as error:
                print(
                    f"collector_orderbook_rotation_warning={type(error).__name__}: {error}",
                    flush=True,
                )

        if not state["console_hidden"] and login_state == "connected":
            state["console_hidden"] = base.hide_console_after_login_if_requested()

        now_mono = time.monotonic()
        if now_mono - state["last_status_at"] >= 1.0:
            try:
                base.publish_collector_status(
                    sender,
                    provider,
                    {
                        "provider_started": True,
                        "registered_count_requested": requested_count,
                        "registered_count": actual_count if realreg_succeeded else 0,
                        "collector_ready": realreg_succeeded and actual_count > 0,
                        "collector_mode": "main_thread_light",
                        "qt_timer_interval_ms": timer.interval(),
                    },
                )
            except Exception as error:
                print(
                    f"collector_status_warning={type(error).__name__}: {error}",
                    flush=True,
                )
            state["last_status_at"] = now_mono

        if login_state in {"failed", "native_handle_failed"}:
            request_exit(1, status.get("last_error") or login_state)
            return
        if not running:
            request_exit(1, status.get("last_error") or "provider stopped")

    timer.timeout.connect(collector_tick)
    timer.start()

    base.publish_collector_status(
        sender,
        provider,
        {
            "provider_started": True,
            "registered_count_requested": requested_count,
            "registered_count": 0,
            "collector_ready": False,
            "collector_mode": "main_thread_light",
            "qt_timer_interval_ms": timer.interval(),
        },
    )
    state["last_status_at"] = time.monotonic()
    print(
        f"qt_event_loop=exec_ startup_timer_ms={startup_interval_ms}",
        flush=True,
    )

    app_result = 0
    try:
        app_result = int(app.exec_() or 0)
    except KeyboardInterrupt:
        request_exit(0)
    except Exception as error:
        print(
            f"collector_qt_event_loop_error={type(error).__name__}: {error}",
            flush=True,
        )
        state["exit_code"] = 1
        state["exit_error"] = str(error)
    finally:
        try:
            timer.stop()
            timer.deleteLater()
        except Exception:
            pass
        sender.stop()
        try:
            provider.stop()
        except Exception:
            pass
        if sender.is_alive():
            sender.join(timeout=2.0)

    if state["exit_error"]:
        print(f"collector_exit_error={state['exit_error']}", flush=True)
    return int(state["exit_code"] or app_result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
