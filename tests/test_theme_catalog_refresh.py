from __future__ import annotations

from pathlib import Path

from realtime_v2.theme_catalog_refresh32 import (
    _normalize_code,
    _parse_member_codes,
    _parse_theme_groups,
)


def test_theme_group_parser_accepts_kiwoom_koa_format():
    assert _parse_theme_groups("100|제습기;200|정유;300|해운;") == [
        ("100", "제습기"),
        ("200", "정유"),
        ("300", "해운"),
    ]


def test_theme_member_parser_normalizes_and_deduplicates_codes():
    assert _parse_member_codes("A005930;000660;005930_AL;000660;") == [
        "000660",
        "005930",
    ]
    assert _normalize_code("A042660") == "042660"


def test_refresh_is_one_shot_offline_and_does_not_touch_realtime_path():
    source = Path("realtime_v2/theme_catalog_refresh32.py").read_text(encoding="utf-8")
    assert '"GetThemeGroupList"' in source
    assert '"GetThemeGroupCode"' in source
    assert "KOA_Functions(QString, QString)" in source
    assert "collector32.pid" in source
    assert "Stop StockBoard before refreshing" in source
    assert "SetRealReg" not in source
    assert "CommRqData" not in source
    assert "SetInputValue" not in source
    assert "QTimer.singleShot(0, refresh_catalog)" in source


def test_safe_launcher_prefers_runtime_catalog_with_static_fallback():
    launcher = Path("stockboard_v2_large.cmd").read_text(encoding="utf-8")
    assert "STOCKBOARD_THEME_MEMBERSHIP_FILE" in launcher
    assert "data\\runtime\\stockboard_v2\\theme_membership.json" in launcher
    assert "static 10-theme fallback" in launcher


def test_refresh_command_always_uses_project_directory_and_32bit_python():
    wrapper = Path("refresh_theme_catalog.cmd").read_text(encoding="utf-8")
    powershell = Path("scripts/refresh_theme_catalog.ps1").read_text(encoding="utf-8")
    assert "cd /d C:\\aiTrade" in wrapper
    assert '$ProjectRoot = "C:\\aiTrade"' in powershell
    assert "Python310-32\\python.exe" in powershell
    assert "collector32.pid" in powershell
    assert "offline_once_no_realtime_collector_work" in powershell
