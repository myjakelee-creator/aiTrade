from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "realtime_v2" / "worker64_guarded_large_bidask.py"
HUB_PATCH = ROOT / "realtime_v2" / "worker_board_data_hub_patch.py"
TR_PATCH = ROOT / "realtime_v2" / "worker_tr_singleflight_patch.py"
CONTEXT_ENTRY = ROOT / "realtime_v2" / "context_snapshot_writer_singleflight.py"


def test_hub_and_singleflight_modules_are_valid_python():
    for path in (
        ROOT / "realtime_v2" / "board_data_hub.py",
        ROOT / "realtime_v2" / "tr_singleflight.py",
        HUB_PATCH,
        TR_PATCH,
        CONTEXT_ENTRY,
    ):
        ast.parse(path.read_text(encoding="utf-8"))


def test_hub_wraps_final_heavy_snapshot_before_background_cache_captures_it():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    hub_call = source.rindex("_install_board_data_hub_fail_open()")
    tr_call = source.rindex("_install_tr_singleflight_fail_open()")
    cache_call = source.rindex("_install_opening_burst_cache_fail_open()")
    assert hub_call < cache_call
    assert tr_call < cache_call


def test_hub_exposes_read_only_shared_endpoints_and_no_direct_board_tr():
    source = HUB_PATCH.read_text(encoding="utf-8")
    assert '"/api/v2/hub/manifest"' in source
    assert '"/api/v2/hub/canonical"' in source
    assert '"/api/v2/hub/features"' in source
    assert '"/api/v2/hub/projection"' in source
    assert 'self.status["board_data_hub_direct_board_tr_allowed"] = False' in source
    assert "hub.publish_feature_snapshot(payload)" in source


def test_existing_slow_sources_use_or_have_singleflight_entries():
    worker_source = TR_PATCH.read_text(encoding="utf-8")
    context_source = CONTEXT_ENTRY.read_text(encoding="utf-8")
    assert 'tr_code="ka90004_program_net"' in worker_source
    assert 'tr_code="market_supply_bundle"' in context_source
    assert 'tr_code="ka10086_ohlc_bootstrap_bundle"' in context_source
    assert 'tr_code="us_market_bundle"' in context_source
    assert "coordinator.execute(" in worker_source
    assert context_source.count("coordinator.execute(") >= 3
