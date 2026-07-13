from __future__ import annotations

"""Compatibility entrypoint for the shared single-flight context writer.

The implementation lives in ``context_snapshot_writer_singleflight``. This file
may be executed by path from Windows launchers, so the repository root must be
available before importing the ``realtime_v2`` package.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.context_snapshot_writer_singleflight import *  # noqa: F401,F403,E402
from realtime_v2.context_snapshot_writer_singleflight import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
