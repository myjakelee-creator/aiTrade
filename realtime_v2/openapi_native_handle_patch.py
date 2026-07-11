from __future__ import annotations

import os
from typing import Any


def _ensure_openapi_native_handle(provider) -> int:
    """Create and validate the QAxWidget HWND before CommConnect.

    Some Windows installations do not materialize a native window handle for an
    unshown top-level QAxWidget. Kiwoom opstarter then exits with
    "핸들값이 없습니다". Moving the control off-screen and showing it briefly keeps
    the login host invisible while forcing a stable native HWND.
    """

    lock = getattr(provider, "_lock", None)
    if lock is None:
        raise RuntimeError("OpenAPI provider lock is unavailable")

    with lock:
        control = getattr(provider, "_control", None)
        app = getattr(provider, "_app", None)

    if control is None:
        raise RuntimeError("OpenAPI QAxWidget is unavailable before login")
    if app is None:
        raise RuntimeError("OpenAPI QApplication is unavailable before login")

    try:
        # WA_NativeWindow is the strongest Qt hint, but winId() below is still the
        # final handle-creation operation. Keep the import optional for test doubles.
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
    except Exception as error:
        raise RuntimeError(f"OpenAPI native HWND creation failed: {error}") from error

    if hwnd <= 0:
        raise RuntimeError(f"OpenAPI native HWND is invalid: {hwnd}")

    if os.name == "nt":
        try:
            import ctypes

            if not bool(ctypes.windll.user32.IsWindow(hwnd)):
                raise RuntimeError(f"OpenAPI HWND is not a valid Windows window: {hwnd}")
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(f"OpenAPI HWND validation failed: {error}") from error

    with lock:
        provider._stockboard_openapi_native_hwnd = hwnd
        provider._stockboard_openapi_native_handle_ready = True
        provider._stockboard_openapi_native_handle_error = None

    return hwnd


def install() -> None:
    import kiwoom_data_provider as provider_module

    provider_class = provider_module.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_openapi_native_handle_installed", False):
        return

    original_request_login = provider_class._request_login
    original_status = provider_class.status

    def request_login(provider):
        if bool(getattr(provider, "_login_requested", False)):
            return original_request_login(provider)
        try:
            _ensure_openapi_native_handle(provider)
        except Exception as error:
            with provider._lock:
                provider._stockboard_openapi_native_handle_ready = False
                provider._stockboard_openapi_native_handle_error = (
                    f"{type(error).__name__}: {error}"
                )
                provider._login_state = "native_handle_failed"
                provider._last_error = str(error)
            raise
        return original_request_login(provider)

    def status(provider) -> dict[str, Any]:
        result = original_status(provider)
        result = result if isinstance(result, dict) else {"status": result}
        result.update(
            {
                "openapi_native_handle_ready": bool(
                    getattr(provider, "_stockboard_openapi_native_handle_ready", False)
                ),
                "openapi_native_hwnd": getattr(
                    provider, "_stockboard_openapi_native_hwnd", None
                ),
                "openapi_native_handle_error": getattr(
                    provider, "_stockboard_openapi_native_handle_error", None
                ),
            }
        )
        return result

    provider_class._request_login = request_login
    provider_class.status = status
    provider_class._stockboard_openapi_native_handle_installed = True
