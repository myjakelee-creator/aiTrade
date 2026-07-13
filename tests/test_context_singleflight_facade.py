from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_historical_context_writer_path_is_singleflight_facade():
    facade = (ROOT / "realtime_v2" / "context_snapshot_writer.py").read_text(
        encoding="utf-8"
    )
    assert "context_snapshot_writer_singleflight" in facade
    assert "fetch_live_market_supply_snapshot" not in facade
    assert "fetch_ohlc_bootstrap" not in facade


def test_singleflight_writer_uses_preserved_base_without_recursion():
    source = (
        ROOT / "realtime_v2" / "context_snapshot_writer_singleflight.py"
    ).read_text(encoding="utf-8")
    assert 'import_module("realtime_v2.context_snapshot_writer_base")' in source
    assert 'import_module("realtime_v2.context_snapshot_writer")' not in source
    assert "coordinator.execute(" in source
    assert 'tr_code="market_supply_bundle"' in source
    assert 'tr_code="ka10086_ohlc_bootstrap_bundle"' in source


def test_preserved_base_contains_only_original_context_implementation():
    source = (
        ROOT / "realtime_v2" / "context_snapshot_writer_base.py"
    ).read_text(encoding="utf-8")
    assert "def fetch_live_market_supply_snapshot" in source
    assert "def fetch_ohlc_bootstrap" in source
    assert "def main()" in source
    assert "tr_singleflight" not in source
