from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_large_launcher_uses_safe_powershell_entrypoint():
    wrapper = (ROOT / "stockboard_v2_large.cmd").read_text(encoding="utf-8")
    assert "scripts\\stockboard_v2_large_safe.ps1" in wrapper
    assert "Stop-OpenApiStarterArtifacts" not in wrapper


def test_safe_launcher_waits_for_real_openapi_connection():
    script = (ROOT / "scripts" / "stockboard_v2_large_safe.ps1").read_text(
        encoding="utf-8"
    )
    assert '$state.LoginState -eq "connected"' in script
    assert "$state.NativeHandleReady" in script
    assert "$state.RegisteredCount -gt 0" in script
    assert "Wait-CollectorOpenApiReady 180" in script


def test_safe_launcher_never_force_kills_opstarter():
    script = (ROOT / "scripts" / "stockboard_v2_large_safe.ps1").read_text(
        encoding="utf-8"
    )
    assert 'Stop-ProcessRows $rows "opstarter"' not in script
    assert "Stop-OpenApiStarterArtifacts" not in script
    assert "Do not terminate opstarter" in script
