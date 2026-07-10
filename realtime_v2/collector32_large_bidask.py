from __future__ import annotations

import importlib
import traceback
from pathlib import Path

large = importlib.import_module("realtime_v2.collector32_large")
base = large.base


def _runtime_dir() -> Path:
    try:
        return Path(base.RUNTIME_DIR)
    except Exception:
        return Path("data/runtime/stockboard_v2")


def _install_orderbook_thin_fail_open() -> None:
    try:
        from realtime_v2.orderbook_thin_scheduler import install as install_orderbook_thin_scheduler

        install_orderbook_thin_scheduler(base)
    except Exception as error:
        # Collector must remain usable even if optional thin bidask scheduler fails.
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

if __name__ == "__main__":
    raise SystemExit(base.main())
