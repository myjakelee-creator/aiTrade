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


def test_singleflight_writer_owns_explicit_production_loop():
    source = (
        ROOT / "realtime_v2" / "context_snapshot_writer_singleflight.py"
    ).read_text(encoding="utf-8")
    assert "def main() -> int:" in source
    assert "def _run_cycle(status: dict) -> None:" in source
    assert "main = base.main" not in source
    assert '"singleflight_explicit_loop_v2"' in source
    assert "_run_cycle(status)" in source


def test_singleflight_owner_is_injected_into_every_status_file_write():
    source = (
        ROOT / "realtime_v2" / "context_snapshot_writer_singleflight.py"
    ).read_text(encoding="utf-8")
    assert "_original_atomic_write = base._atomic_write" in source
    assert "def _inject_context_status" in source
    assert 'status["context_owner"] = "tr_singleflight"' in source
    assert 'status["context_entrypoint"] = "realtime_v2.context_snapshot_writer"' in source
    assert 'status["context_process_pid"] = os.getpid()' in source
    assert 'status["context_runtime_version"] = _CONTEXT_RUNTIME_VERSION' in source
    assert 'status["tr_singleflight"] = coordinator.status()' in source
    assert "if target == Path(base.STATUS_FILE):" in source
    assert "base._atomic_write = _atomic_write" in source


def test_write_status_runtime_injects_owner_and_coordinator(monkeypatch):
    from realtime_v2 import context_snapshot_writer_singleflight as writer

    captured = []
    monkeypatch.setattr(writer, "_original_write_status", captured.append)
    monkeypatch.setattr(
        writer.coordinator,
        "status",
        lambda: {"enabled": True, "request_count": 3},
    )

    writer.write_status({"us_market_status": "ok"})

    assert len(captured) == 1
    payload = captured[0]
    assert payload["context_owner"] == "tr_singleflight"
    assert payload["context_entrypoint"] == "realtime_v2.context_snapshot_writer"
    assert payload["context_process_pid"] > 0
    assert payload["context_runtime_version"] == "singleflight_explicit_loop_v2"
    assert payload["tr_singleflight"]["request_count"] == 3


def test_preserved_base_contains_only_original_context_implementation():
    source = (
        ROOT / "realtime_v2" / "context_snapshot_writer_base.py"
    ).read_text(encoding="utf-8")
    assert "def fetch_live_market_supply_snapshot" in source
    assert "def fetch_ohlc_bootstrap" in source
    assert "def main()" in source
    assert "tr_singleflight" not in source
