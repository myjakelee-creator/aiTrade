from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_production_safe_launcher_owns_verified_context_writer_once():
    safe = _text("scripts/stockboard_v2_large_safe.ps1")

    assert 'scripts\\start_context_singleflight.ps1' in safe
    assert safe.count("Start-VerifiedContextWriter") == 2  # definition + one call
    assert 'ArgumentList @("realtime_v2\\context_snapshot_writer.py"' not in safe
    assert "ContextWriterCount" in safe
    assert "LegacyContextWriterDetected" in safe


def test_outer_cmd_does_not_start_or_defer_context_writer():
    launcher = _text("stockboard_v2_large.cmd")

    assert "CONTEXT_SINGLEFLIGHT" not in launcher
    assert "STOCKBOARD_CONTEXT_DEFER_TO_VERIFIED_LAUNCHER" not in launcher
    assert "start_context_singleflight.ps1" not in launcher
    assert "The safe launcher owns the single verified portable-v2 context writer" in launcher


def test_verified_context_launcher_requires_one_portable_v2_owner():
    context = _text("scripts/start_context_singleflight.ps1")

    assert '$ExpectedEntrypoint = "realtime_v2.context_snapshot_writer_portable_v2"' in context
    assert '$ExpectedParser = "exact_daily_row_fields_v2"' in context
    assert "$rows.Count -eq 1" in context
    assert "$ownerRows.Count -eq 1" in context
    assert "$legacyRows.Count -eq 0" in context
    assert "context_writer_process_count" in context
    assert "legacy_context_writer_detected" in context
    assert "readiness_timeout_or_multiple_owners" in context


def test_stop_patterns_cover_legacy_and_portable_context_variants():
    safe = _text("scripts/stockboard_v2_large_safe.ps1")
    context = _text("scripts/start_context_singleflight.ps1")

    expected_pattern = (
        r"context_snapshot_writer(?:_(?:base|singleflight|portable(?:_v2)?))?"
    )
    assert expected_pattern in safe
    assert expected_pattern in context
