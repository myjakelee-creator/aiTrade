from __future__ import annotations

import argparse
import ctypes
import json
import os
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
OUTPUT_PATH = RUNTIME_DIR / "theme_membership.json"
STATUS_PATH = RUNTIME_DIR / "theme_catalog_refresh_status.json"
COLLECTOR_PID_PATH = RUNTIME_DIR / "collector32.pid"
OPENAPI_CONTROL = "KHOPENAPI.KHOpenAPICtrl.1"


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    return True


def _normalize_code(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def _parse_theme_groups(raw_value: Any) -> list[tuple[str, str]]:
    groups: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in str(raw_value or "").split(";"):
        text = item.strip()
        if not text or "|" not in text:
            continue
        theme_id, theme_name = (part.strip() for part in text.split("|", 1))
        if not theme_id or not theme_name or theme_id in seen:
            continue
        seen.add(theme_id)
        groups.append((theme_id, theme_name))
    return groups


def _parse_member_codes(raw_value: Any) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    text = str(raw_value or "").replace(",", ";")
    for item in text.split(";"):
        code = _normalize_code(item)
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return sorted(codes)


def _build_catalog(api) -> dict[str, Any]:
    raw_groups = ""
    groups: list[tuple[str, str]] = []
    list_mode = "0"
    for candidate_mode in ("0", "1"):
        raw_groups = api.dynamicCall(
            "KOA_Functions(QString, QString)",
            "GetThemeGroupList",
            candidate_mode,
        )
        groups = _parse_theme_groups(raw_groups)
        if groups:
            list_mode = candidate_mode
            break

    if not groups:
        raise RuntimeError("GetThemeGroupList returned no valid themes")

    themes: list[dict[str, Any]] = []
    membership_count = 0
    empty_theme_count = 0
    for theme_id, theme_name in groups:
        raw_codes = api.dynamicCall(
            "KOA_Functions(QString, QString)",
            "GetThemeGroupCode",
            theme_id,
        )
        codes = _parse_member_codes(raw_codes)
        if not codes:
            empty_theme_count += 1
            continue
        membership_count += len(codes)
        themes.append(
            {
                "theme_id": theme_id,
                "theme_name": theme_name,
                "short_name": theme_name,
                "min_active_members": min(3, len(codes)),
                "members": [
                    {
                        "stock_code": code,
                        "weight": 1.0,
                        "relation": "kiwoom_theme_member",
                    }
                    for code in codes
                ],
            }
        )

    if not themes:
        raise RuntimeError("GetThemeGroupCode returned no usable theme members")

    generated_at = _now_text()
    return {
        "schema_version": 2,
        "master_version": f"KIWOOM_THEME_{datetime.now():%Y%m%d_%H%M%S}",
        "source": "kiwoom_koa_functions",
        "generated_at": generated_at,
        "refresh_policy": "offline_manual_no_realtime_collector_work",
        "theme_list_mode": list_mode,
        "theme_count": len(themes),
        "membership_count": membership_count,
        "empty_theme_count": empty_theme_count,
        "themes": themes,
    }


def _write_status(status: str, **values: Any) -> None:
    payload = {
        "schema_version": 1,
        "source": "theme_catalog_refresh32",
        "status": status,
        "ts": _now_text(),
        "output_path": str(OUTPUT_PATH),
        **values,
    }
    _atomic_write_json(STATUS_PATH, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the Kiwoom theme catalog once while StockBoard is stopped."
    )
    parser.add_argument("--timeout-sec", type=int, default=180)
    args = parser.parse_args(argv)

    if struct.calcsize("P") * 8 != 32:
        message = "theme catalog refresh requires 32-bit Python"
        _write_status("error", last_error=message)
        print(message, file=sys.stderr)
        return 2

    collector_pid = _read_pid(COLLECTOR_PID_PATH)
    if _pid_alive(collector_pid):
        message = (
            f"StockBoard collector PID {collector_pid} is running. "
            "Stop StockBoard before refreshing the theme catalog."
        )
        _write_status("blocked", collector_pid=collector_pid, last_error=message)
        print(message, file=sys.stderr)
        return 3

    try:
        from PyQt5.QAxContainer import QAxWidget
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication
    except Exception as error:
        message = f"PyQt5 QAx import failed: {type(error).__name__}: {error}"
        _write_status("error", last_error=message)
        print(message, file=sys.stderr)
        return 4

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    api = QAxWidget()
    if not api.setControl(OPENAPI_CONTROL):
        message = f"failed to create {OPENAPI_CONTROL}"
        _write_status("error", last_error=message)
        print(message, file=sys.stderr)
        return 5

    result_code = {"value": 1}
    finished = {"value": False}

    def finish(code: int) -> None:
        if finished["value"]:
            return
        finished["value"] = True
        result_code["value"] = code
        app.exit(code)

    def fail(message: str, code: int = 6) -> None:
        _write_status("error", last_error=message)
        print(message, file=sys.stderr)
        finish(code)

    def refresh_catalog() -> None:
        try:
            payload = _build_catalog(api)
            _atomic_write_json(OUTPUT_PATH, payload)
            _write_status(
                "ok",
                theme_count=payload.get("theme_count"),
                membership_count=payload.get("membership_count"),
                master_version=payload.get("master_version"),
                generated_at=payload.get("generated_at"),
                last_error="",
            )
            print(f"THEME_CATALOG_READY=True themes={payload['theme_count']} memberships={payload['membership_count']}")
            print(f"THEME_CATALOG_PATH={OUTPUT_PATH}")
            finish(0)
        except Exception as error:
            fail(f"theme catalog build failed: {type(error).__name__}: {error}", 7)

    def on_event_connect(error_code: int) -> None:
        if int(error_code) != 0:
            fail(f"OpenAPI login failed: error_code={error_code}", 8)
            return
        QTimer.singleShot(0, refresh_catalog)

    api.OnEventConnect.connect(on_event_connect)

    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(
        lambda: fail(f"theme catalog refresh timed out after {args.timeout_sec}s", 9)
    )
    timeout_timer.start(max(10, int(args.timeout_sec)) * 1000)

    _write_status("login_requested", collector_pid=collector_pid, last_error="")
    connect_result = api.dynamicCall("CommConnect()")
    if connect_result not in (None, 0):
        fail(f"CommConnect returned {connect_result}", 10)
    elif not finished["value"]:
        app.exec_()

    timeout_timer.stop()
    api.deleteLater()
    return int(result_code["value"])


if __name__ == "__main__":
    raise SystemExit(main())
