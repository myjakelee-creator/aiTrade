from __future__ import annotations

"""Compatibility entrypoint for the shared single-flight context writer.

The implementation lives in ``context_snapshot_writer_singleflight``. This file
may be executed by path from older Windows launchers, so the repository root must
be available before importing the ``realtime_v2`` package.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# stockboard_v2_large.cmd starts the verified module owner after the main
# worker/collector startup. Prevent the older safe launcher from starting a
# second Context process or issuing duplicate TR requests during that window.
if (
    __name__ == "__main__"
    and os.getenv("STOCKBOARD_CONTEXT_DEFER_TO_VERIFIED_LAUNCHER", "0") == "1"
):
    raise SystemExit(0)

from realtime_v2.context_snapshot_writer_singleflight import *  # noqa: F401,F403,E402
from realtime_v2.context_snapshot_writer_singleflight import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
