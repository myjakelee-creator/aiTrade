from __future__ import annotations

import argparse
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import realtime_v2.collector32 as base  # noqa: E402
from realtime_v2.common import (  # noqa: E402
    LARGE_TRADE_THRESHOLD_KRW,
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    normalized_price,
    now_text,
    to_int,
    trading_date_text,
)

_STATUS_PATH = RUNTIME_DIR / "large_trade_sidecar_status.json"
_REALTIME_FIDS = "10;15;20"
_SCREEN_START = 5300
_BATCH_SIZE = 100
_MODE = "isolated_fid15_large_trade_sidecar_v1"


def tracking_limit_for_time(
    current: datetime,
    *,
    opening_limit: int,
    normal_limit: int,
) -> int:
    minutes = current.hour * 60 + current.minute
    if 9 * 60 <= minutes < 9 * 60 + 10:
        return max(1, min(normal_limit, opening_limit))
    return max(1, normal_limit)


def large_trade_delta(price_raw: Any, qty_raw: Any) -> dict[str, Any] | None:
    price = normalized_price(price_raw)
    qty = to_int(qty_raw)
    if price is None or price <= 0 or qty in (None, 0):
        return None
    amount_krw = abs(int(qty)) * int(price)
    if amount_krw < LARGE_TRADE_THRESHOLD_KRW:
        return None
    amount_eok = round(amount_krw / 100_000_000, 4)
    return {
        "large_trade_buy_count_delta": 1 if qty > 0 else 0,
        "large_trade_sell_count_delta": 1 if qty < 0 else 0,
        "large_trade_buy_sum_eok_delta": amount_eok if qty > 0 else 0.0,
        "large_trade_sell_sum_eok_delta": amount_eok if qty < 0 else 0.0,
        "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
        "large_trade_price": price,
        "large_trade_qty": qty,
    }


class SidecarStatus:
    def __init__(self, *, total_code_count: int, opening_limit: int, normal_limit: int):
        self.lock = threading.RLock()
        self.started_at = now_text()
        self.running = False
        self.login_state = "not_requested"
        self.login_error_code: Any = None
        self.login_completed_at: str | None = None
        self.registered_limit = 0
        self.registered_code_count = 0
        self.registered_screens: list[str] = []
        self.registration_error: str | None = None
        self.total_code_count = total_code_count
        self.opening_limit = opening_limit
        self.normal_limit = normal_limit
        self.realdata_received_count = 0
        self.fid15_read_count = 0
        self.large_trade_event_count = 0
        self.large_trade_buy_count = 0
        self.large_trade_sell_count = 0
        self.large_trade_sum_eok = 0.0
        self.last_received_at: str | None = None
        self.last_large_trade_at: str | None = None
        self.last_large_trade_code: str | None = None
        self.last_error: str | None = None
        self.native_hwnd: int | None = None

    def payload(self) -> dict[str, Any]:
        with self.lock:
            return {
                "schema_version": 1,
                "mode": _MODE,
                "pid": os.getpid(),
                "started_at": self.started_at,
                "updated_at": now_text(),
                "running": self.running,
                "login_state": self.login_state,
                "login_error_code": self.login_error_code,
                "login_completed_at": self.login_completed_at,
                "realtime_fids": _REALTIME_FIDS,
                "total_code_count": self.total_code_count,
                "opening_limit": self.opening_limit,
                "normal_limit": self.normal_limit,
                "registered_limit": self.registered_limit,
                "registered_code_count": self.registered_code_count,
                "registered_screens": list(self.registered_screens),
                "registration_error": self.registration_error,
                "realdata_received_count": self.realdata_received_count,
                "fid15_read_count": self.fid15_read_count,
                "large_trade_event_count": self.large_trade_event_count,
                "large_trade_buy_count": self.large_trade_buy_count,
                "large_trade_sell_count": self.large_trade_sell_count,
                "large_trade_sum_eok": round(self.large_trade_sum_eok, 4),
                "last_received_at": self.last_received_at,
                "last_large_trade_at": self.last_large_trade_at,
                "last_large_trade_code": self.last_large_trade_code,
                "last_error": self.last_error,
                "native_hwnd": self.native_hwnd,
                "price_collector_isolation": True,
                "network_policy": "publish only >=50000000 KRW delta events",
            }


def _real_text(control, code: str, fid: int) -> str:
    value = control.dynamicCall("GetCommRealData(QString, int)", code, int(fid))
    return str(value or "").strip()


def _native_hwnd(app, control, status: SidecarStatus) -> int:
    try:
        from PyQt5.QtCore import Qt

        control.setAttribute(Qt.WA_NativeWindow, True)
    except Exception:
        pass
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
    with status.lock:
        status.native_hwnd = hwnd
    return hwnd


def _batches(codes: list[str]):
    for index in range(0, len(codes), _BATCH_SIZE):
        yield str(_SCREEN_START + index // _BATCH_SIZE), codes[index : index + _BATCH_SIZE]


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard isolated large-trade FID15 sidecar")
    parser.add_argument("--host", default=base.DEFAULT_HOST)
    parser.add_argument("--event-port", type=int, default=base.DEFAULT_EVENT_PORT)
    parser.add_argument(
        "--codes-file",
        default=str(RUNTIME_DIR / "codes.txt"),
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--opening-limit", type=int, default=20)
    parser.add_argument("--suffix", default="AL")
    parser.add_argument("--flush-ms", type=int, default=50)
    args = parser.parse_args()

    normal_limit = max(1, min(100, int(args.limit or 100)))
    opening_limit = max(1, min(normal_limit, int(args.opening_limit or 20)))
    all_codes = base.load_codes(args.codes_file, "", normal_limit, args.suffix)
    sender = base.EventSender(args.host, args.event_port, flush_ms=args.flush_ms)
    sender.start()
    status = SidecarStatus(
        total_code_count=len(all_codes),
        opening_limit=opening_limit,
        normal_limit=normal_limit,
    )

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
    _native_hwnd(app, control, status)

    active_screens: list[str] = []

    def write_status() -> None:
        try:
            atomic_write_json(_STATUS_PATH, status.payload())
        except Exception as error:
            with status.lock:
                status.last_error = f"status_write: {type(error).__name__}: {error}"

    def remove_registration() -> None:
        nonlocal active_screens
        for screen in list(active_screens):
            try:
                control.dynamicCall("SetRealRemove(QString, QString)", screen, "ALL")
            except Exception:
                pass
        active_screens = []

    def register_for_limit(limit: int) -> None:
        nonlocal active_screens
        limit = max(1, min(len(all_codes), int(limit)))
        with status.lock:
            if status.registered_limit == limit and status.registered_code_count == limit:
                return
        remove_registration()
        screens: list[str] = []
        try:
            target_codes = all_codes[:limit]
            for screen, batch in _batches(target_codes):
                result = control.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    screen,
                    ";".join(batch),
                    _REALTIME_FIDS,
                    "0",
                )
                if result not in (None, 0, "0"):
                    raise RuntimeError(f"SetRealReg screen {screen} returned {result!r}")
                screens.append(screen)
            active_screens = screens
            with status.lock:
                status.registered_limit = limit
                status.registered_code_count = len(target_codes)
                status.registered_screens = list(screens)
                status.registration_error = None
                status.last_error = None
        except Exception as error:
            with status.lock:
                status.registered_limit = 0
                status.registered_code_count = 0
                status.registered_screens = []
                status.registration_error = f"{type(error).__name__}: {error}"
                status.last_error = status.registration_error
            raise

    def on_event_connect(error_code) -> None:
        try:
            error_value = int(error_code)
        except (TypeError, ValueError):
            error_value = error_code
        with status.lock:
            status.login_error_code = error_value
            status.login_completed_at = now_text()
            status.login_state = "connected" if error_value == 0 else "failed"
            status.last_error = None if error_value == 0 else f"OnEventConnect failed: {error_value}"
        if error_value != 0:
            write_status()
            app.exit(1)
            return
        try:
            register_for_limit(
                tracking_limit_for_time(
                    datetime.now(),
                    opening_limit=opening_limit,
                    normal_limit=normal_limit,
                )
            )
        except Exception:
            write_status()
            app.exit(1)
            return
        write_status()

    def on_receive_real_data(received_code, real_type, _real_data) -> None:
        now = now_text()
        with status.lock:
            status.realdata_received_count += 1
            status.last_received_at = now
        if "주식체결" not in str(real_type or ""):
            return
        code = normalize_code(received_code)
        if not code:
            return
        try:
            price_raw = _real_text(control, str(received_code), 10)
            qty_raw = _real_text(control, str(received_code), 15)
            with status.lock:
                status.fid15_read_count += 1
            delta = large_trade_delta(price_raw, qty_raw)
            if delta is None:
                return
            trade_time = _real_text(control, str(received_code), 20)
            delta.update(
                {
                    "large_trade_source": "fid15_sidecar",
                    "large_trade_status": "ok",
                    "large_trade_updated_at": now,
                    "large_trade_trade_time": trade_time,
                    "trading_date": trading_date_text(),
                }
            )
            sender.publish_direct(
                {
                    "type": "large_trade_delta",
                    "ts": now,
                    "stock_code": code,
                    "received_code": str(received_code),
                    "values": delta,
                }
            )
            with status.lock:
                status.large_trade_event_count += 1
                status.large_trade_buy_count += int(delta["large_trade_buy_count_delta"])
                status.large_trade_sell_count += int(delta["large_trade_sell_count_delta"])
                status.large_trade_sum_eok += float(
                    delta["large_trade_buy_sum_eok_delta"]
                    or delta["large_trade_sell_sum_eok_delta"]
                )
                status.last_large_trade_at = now
                status.last_large_trade_code = code
                status.last_error = None
        except Exception as error:
            # A sidecar parsing/read error must never affect the price collector.
            with status.lock:
                status.last_error = f"callback: {type(error).__name__}: {error}"

    def periodic() -> None:
        if status.login_state == "connected":
            wanted = tracking_limit_for_time(
                datetime.now(),
                opening_limit=opening_limit,
                normal_limit=normal_limit,
            )
            try:
                register_for_limit(wanted)
            except Exception:
                pass
        write_status()

    control.OnEventConnect.connect(on_event_connect)
    control.OnReceiveRealData.connect(on_receive_real_data)

    timer = QTimer()
    timer.setInterval(1000)
    timer.timeout.connect(periodic)
    timer.start()

    with status.lock:
        status.running = True
        status.login_state = "requested"
    write_status()
    print(
        f"large_trade_sidecar mode={_MODE} codes={len(all_codes)} "
        f"opening_limit={opening_limit} normal_limit={normal_limit}",
        flush=True,
    )
    result = control.dynamicCall("CommConnect()")
    if result not in (None, 0, "0"):
        with status.lock:
            status.login_state = "failed"
            status.login_error_code = result
            status.last_error = f"CommConnect returned {result!r}"
        write_status()
        sender.stop()
        return 1

    app_result = 0
    try:
        app_result = int(app.exec_() or 0)
    except KeyboardInterrupt:
        app_result = 0
    finally:
        with status.lock:
            status.running = False
        remove_registration()
        write_status()
        sender.stop()
        if sender.is_alive():
            sender.join(timeout=2.0)
    return app_result


if __name__ == "__main__":
    raise SystemExit(main())
