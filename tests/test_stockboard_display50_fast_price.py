from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "realtime_v2" / "worker64_guarded_large_bidask.py"


def source_text() -> str:
    return SOURCE_PATH.read_text(encoding="utf-8")


def test_display100_patch_is_valid_python():
    ast.parse(source_text())


def test_browser_requests_only_top100_rows():
    source = source_text()
    assert '"/api/v2/snapshot?limit=100&ts=${Date.now()}"' in source
    assert '"/api/v2/stream?limit=100&interval_ms=100&ts=${Date.now()}"' in source
    assert '"/api/v2/snapshot?limit=50&ts=${Date.now()}"' not in source
    assert '"/api/v2/stream?limit=50&interval_ms=100&ts=${Date.now()}"' not in source
    assert 'patched = patched.replace("Top300 Pool", "표시 Pool")' in source
    assert "STOCKBOARD_V2_DISPLAY100_FAST_PRICE_20260714" in source


def test_price_and_rate_are_patched_without_full_table_rerender():
    source = source_text()
    assert "function __sbv2FastPatchPriceRate(payload)" in source
    assert "const priceCell = tr.cells && tr.cells[4]" in source
    assert "const rateCell = tr.cells && tr.cells[5]" in source
    assert "__sbv2HandleFastSnapshot(JSON.parse(e.data))" in source
    assert "const __sbv2HeavyRenderIntervalMs = 500" in source


def test_heavy_pool_render_is_slow_lane():
    source = source_text()
    assert '"return 1000;"' in source
    assert "표시 ${payload.row_count||raw.length} / 내부 ${payload.status?.universe_count||raw.length}" in source


def test_internal_universe_and_collector_limits_are_not_changed_here():
    source = source_text()
    assert "build_universe" not in source
    assert "STOCKBOARD_V2_COLLECTOR_LIMIT" not in source
    assert "STOCKBOARD_REALTIME_CODE_LIMIT" not in source
