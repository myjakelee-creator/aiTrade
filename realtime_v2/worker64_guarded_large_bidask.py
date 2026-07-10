from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

large = importlib.import_module("realtime_v2.worker64_guarded_large")
base = large.base


def _runtime_dir() -> Path:
    try:
        return Path(base.RUNTIME_DIR)
    except Exception:
        return ROOT / "data" / "runtime" / "stockboard_v2"


def _write_patch_error(filename: str, error: Exception) -> None:
    try:
        runtime = _runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / filename).write_text(
            f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass


def _install_bidask_patch_fail_open() -> None:
    try:
        from realtime_v2.bidask_last_cache_patch import install as install_bidask_last_cache

        install_bidask_last_cache(base)
    except Exception as error:
        # The web worker must never die because an optional display/cache patch failed.
        _write_patch_error("bidask_worker_patch_error.txt", error)


def _install_display_hold_fail_open() -> None:
    try:
        from realtime_v2.display_hold_policy_patch import install as install_display_hold

        install_display_hold(base)
    except Exception as error:
        # Optional display carry-forward policy must never kill the worker.
        _write_patch_error("display_hold_patch_error.txt", error)


def _install_display_hold_ohlc_price_fail_open() -> None:
    try:
        from realtime_v2.display_hold_ohlc_price_patch import install as install_ohlc_price_hold

        install_ohlc_price_hold(base)
    except Exception as error:
        # OHLC-derived close-price fallback is optional; keep worker alive on failure.
        _write_patch_error("display_hold_ohlc_price_patch_error.txt", error)


def _install_scroll_focus_independent_patch_fail_open() -> None:
    try:
        from realtime_v2.scroll_focus_independent_patch import install as install_scroll_focus_independent

        install_scroll_focus_independent(large)
    except Exception as error:
        # Scroll/focus behavior is UI-only; never let it stop the worker.
        _write_patch_error("scroll_focus_independent_patch_error.txt", error)


def _install_header_sort_patch_fail_open() -> None:
    try:
        from realtime_v2.html_header_sort_patch import install as install_header_sort

        install_header_sort(base, large)
    except Exception as error:
        # Sorting patch is UI-only; keep the web worker alive on any failure.
        _write_patch_error("html_header_sort_patch_error.txt", error)


_install_bidask_patch_fail_open()
_install_display_hold_fail_open()
_install_display_hold_ohlc_price_fail_open()
_install_scroll_focus_independent_patch_fail_open()
_install_header_sort_patch_fail_open()

if __name__ == "__main__":
    raise SystemExit(base.main())
