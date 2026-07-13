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


def test_large_launcher_uses_safe_powershell_entrypoint_and_preflight():
    wrapper = _wrapper()
    assert "scripts\\stockboard_v2_large_safe.ps1" in wrapper
    assert "scripts\\stockboard_v2_openapi_preflight.ps1" in wrapper
    assert "Stop-OpenApiStarterArtifacts" not in wrapper


def test_start_flow_stops_old_runtime_then_cleans_orphan_then_starts():
    wrapper = _wrapper()
    stop_index = wrapper.index('-Action stop')
    preflight_index = wrapper.index('-File "%PREFLIGHT%"')
    start_index = wrapper.index('-Action "%START_ACTION%"')
    assert stop_index < preflight_index < start_index


def test_safe_launcher_waits_for_real_openapi_connection():
    script = _safe_script()
    assert '$state.LoginState -eq "connected"' in script
    assert "$state.NativeHandleReady" in script
    assert "$state.RegisteredCount -gt 0" in script
    assert "Wait-CollectorOpenApiReady 180" in script


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
    assert "interfaces\\\\kiwoom" in script
    assert "Another 32-bit Kiwoom/OpenAPI Python host is running" in script


def test_safe_launcher_trusts_only_build_universe_validated_fallback():
    script = _safe_script()
    assert "validated cached fallback are both unavailable" in script
    assert "using existing universe.json" not in script
    assert 'if (Test-Path -LiteralPath $UniverseFile)' not in script
