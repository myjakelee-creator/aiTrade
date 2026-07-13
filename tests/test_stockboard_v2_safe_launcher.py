from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _wrapper() -> str:
    return (ROOT / "stockboard_v2_large.cmd").read_text(encoding="utf-8")


def _safe_script() -> str:
    return (ROOT / "scripts" / "stockboard_v2_large_safe.ps1").read_text(
        encoding="utf-8"
    )


def _preflight_script() -> str:
    return (ROOT / "scripts" / "stockboard_v2_openapi_preflight.ps1").read_text(
        encoding="utf-8"
    )


def _context_launcher() -> str:
    return (ROOT / "scripts" / "start_context_singleflight.ps1").read_text(
        encoding="utf-8"
    )


def test_large_launcher_uses_safe_powershell_entrypoint_and_preflight():
    wrapper = _wrapper()
    assert r"scripts\stockboard_v2_large_safe.ps1" in wrapper
    assert r"scripts\stockboard_v2_openapi_preflight.ps1" in wrapper
    assert r"scripts\start_context_singleflight.ps1" in wrapper
    assert "Stop-OpenApiStarterArtifacts" not in wrapper


def test_start_flow_requires_verified_context_after_stockboard_start():
    wrapper = _wrapper()
    stop_index = wrapper.index('-Action stop')
    preflight_index = wrapper.index('-File "%PREFLIGHT%"')
    start_index = wrapper.index('-Action "%START_ACTION%"')
    context_index = wrapper.index('-File "%CONTEXT_SINGLEFLIGHT%"')
    assert stop_index < preflight_index < start_index < context_index
    assert 'if errorlevel 1 goto failed' in wrapper


def test_context_launcher_starts_direct_entrypoint_and_checks_readiness():
    script = _context_launcher()
    assert 'context_snapshot_writer_singleflight.py' in script
    assert 'context_snapshot_writer.py' in script
    assert 'context_snapshot_writer_base.py' in script
    assert 'CONTEXT_SINGLEFLIGHT_READY=True' in script
    assert '$owner -eq $ExpectedOwner' in script
    assert '$runtime -eq $ExpectedRuntime' in script
    assert '$statusPid -eq [int]$process.Id' in script
    assert 'Context single-flight writer exited before readiness.' in script
    assert 'Context single-flight readiness was not confirmed within 20 seconds.' in script


def test_wrapper_applies_safe_opening_burst_defaults_without_shrinking_universe():
    wrapper = _wrapper()
    assert 'STOCKBOARD_V2_COLLECTOR_LIMIT=100' in wrapper
    assert 'STOCKBOARD_TRADE_VALUE_SAMPLE_MS=500' in wrapper
    assert 'STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS=500' in wrapper
    assert 'STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS=2000' in wrapper
    assert 'STOCKBOARD_BACKGROUND_REBUILD_POLL_MS=50' in wrapper
    assert 'STOCKBOARD_STATUS_WRITE_INTERVAL_SEC=5' in wrapper
    assert 'if not defined STOCKBOARD_V2_COLLECTOR_LIMIT' in wrapper


def test_safe_launcher_waits_for_actual_login_and_realreg():
    script = _safe_script()
    assert '$state.LoginState -eq "connected"' in script
    assert "$state.RealRegSucceeded" in script
    assert "$state.RegisteredCount -gt 0" in script
    assert "$state.CollectorAlive" in script
    assert "Wait-CollectorOpenApiReady 180" in script
    assert "$state.NativeHandleReady" in script
    ready_block = script[
        script.index("function Wait-CollectorOpenApiReady") : script.index(
            "function Build-Universe"
        )
    ]
    assert "$state.NativeHandleReady -and" not in ready_block


def test_collector_console_and_logs_stay_visible_during_validation():
    script = _safe_script()
    assert 'COLLECTOR_PATH=verified_provider_thread_restore' in script
    assert '$env:STOCKBOARD_HIDE_COLLECTOR_CONSOLE_AFTER_LOGIN = "0"' in script
    assert "COLLECTOR32_STDOUT=" in script
    assert "COLLECTOR32_STDERR=" in script


def test_safe_launcher_never_kills_opstarter_after_collector_start():
    script = _safe_script()
    assert 'Stop-ProcessRows $rows "opstarter"' not in script
    assert "Stop-OpenApiStarterArtifacts" not in script
    assert "Do not terminate opstarter" in script


def test_preflight_kills_only_orphan_opstarter_before_start():
    script = _preflight_script()
    assert "Get-StockBoardCollectorRows" in script
    assert "requires the old StockBoard collector to be stopped first" in script
    assert "Stopping orphan opstarter before collector start" in script
    assert "Start-Sleep -Milliseconds 1200" in script
    assert "OPENAPI_PRESTART_READY=True" in script


def test_preflight_blocks_another_openapi_python_host():
    script = _preflight_script()
    assert "Get-OtherOpenApiPythonRows" in script
    assert "kiwoom_interface_32" in script
    assert r"interfaces\\kiwoom" in script
    assert "Another 32-bit Kiwoom/OpenAPI Python host is running" in script


def test_safe_launcher_trusts_only_build_universe_validated_fallback():
    script = _safe_script()
    assert "validated cached fallback are both unavailable" in script
    assert "using existing universe.json" not in script
    assert 'if (Test-Path -LiteralPath $UniverseFile)' not in script
