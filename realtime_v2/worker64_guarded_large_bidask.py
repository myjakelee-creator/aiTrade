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


def _install_bidask_patch_fail_open() -> None:
    try:
        from realtime_v2.bidask_last_cache_patch import install as install_bidask_last_cache

        install_bidask_last_cache(base)
    except Exception as error:
        # The web worker must never die because an optional display/cache patch failed.
        # Keep the board alive and leave a concrete log file for diagnosis.
        try:
            runtime = _runtime_dir()
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "bidask_worker_patch_error.txt").write_text(
                f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
                encoding="utf-8",
            )
        except Exception:
            pass


_install_bidask_patch_fail_open()

if __name__ == "__main__":
    raise SystemExit(base.main())
