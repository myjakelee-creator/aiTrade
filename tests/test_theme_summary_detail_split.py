from __future__ import annotations

import json
import types
from pathlib import Path

from realtime_v2.theme_projection_summary_split_patch import install


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "realtime_v2" / "theme_projection_engine.py"


def isolated_engine():
    module = types.ModuleType("theme_projection_engine_summary_split_test")
    module.__file__ = str(ENGINE_PATH)
    source = ENGINE_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(ENGINE_PATH), "exec"), module.__dict__)
    install(module)
    return module


def mapping_file(tmp_path: Path) -> Path:
    path = tmp_path / "themes.json"
    path.write_text(
        json.dumps(
            {
                "master_version": "TEST",
                "themes": [
                    {
                        "theme_id": "T1",
                        "theme_name": "테마1",
                        "members": ["000001", "000002"],
                    },
                    {
                        "theme_id": "T2",
                        "theme_name": "테마2",
                        "members": ["000003"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def row(code: str, rate: float, trade: float) -> dict:
    return {
        "stock_code": code,
        "stock_name": code,
        "price": 1000.0,
        "change_rate": rate,
        "trade_value_eok": trade,
        "trade_value_1m_eok": trade / 10,
        "trade_value_5m_eok": trade / 2,
        "amount_ratio": 2.0,
        "candidate_score": 70.0,
        "candidate_grade": "C",
        "execution_strength": 120.0,
        "strength_5m": 110.0,
        "program_net": 1.0,
        "large_trade_net_sum_eok": 0.5,
        "large_trade_net_count": 1,
    }


def test_summary_builds_no_all_theme_member_details(tmp_path):
    module = isolated_engine()
    loader = module.ThemeMembershipLoader(paths=[mapping_file(tmp_path)], refresh_sec=1)
    builder = module.ThemeProjectionBuilder(loader=loader)
    rows = (
        row("000001", 3.0, 100.0),
        row("000002", 1.0, 50.0),
        row("000003", -2.0, 20.0),
    )

    payload = builder(1, rows, {})

    assert payload["status"] == "READY"
    assert payload["theme_count"] == 2
    assert "details" not in payload
    assert payload["performance_breakdown"]["full_theme_detail_rows_built"] == 0
    assert payload["policy"]["summary_only"] is True
    assert payload["policy"]["all_theme_member_detail_generation_allowed"] is False
    assert all("members" not in theme for theme in payload["rows"])


def test_selected_detail_builds_only_requested_theme(tmp_path):
    module = isolated_engine()
    loader = module.ThemeMembershipLoader(paths=[mapping_file(tmp_path)], refresh_sec=1)
    builder = module.ThemeProjectionBuilder(loader=loader)
    rows = (
        row("000001", 3.0, 100.0),
        row("000002", 1.0, 50.0),
        row("000003", -2.0, 20.0),
    )
    builder(1, rows, {})

    detail = builder.build_selected_detail(1, "T1", rows, {})

    assert detail["status"] == "READY"
    assert detail["theme_id"] == "T1"
    assert detail["policy"]["selected_theme_only"] is True
    assert len(detail["theme"]["members"]) == 2
    assert {
        member["stock_code"] for member in detail["theme"]["members"]
    } == {"000001", "000002"}


def test_summary_split_has_no_openapi_or_http_calculation_path():
    source = (
        ROOT / "realtime_v2" / "theme_projection_summary_split_patch.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "dynamicCall",
        "CommRqData",
        "SetRealReg",
        "QAxWidget",
        "kiwoom_data_provider",
    ):
        assert forbidden not in source
    assert "full_theme_detail_rows_built" in source
    assert "build_selected_detail" in source


def test_worker_bootstraps_split_before_hub_and_installs_detail_cache():
    entry = (
        ROOT / "realtime_v2" / "worker64_guarded_large_bidask.py"
    ).read_text(encoding="utf-8")
    split_index = entry.index("install_theme_summary_split(theme_projection_module)")
    hub_import_index = entry.index(
        "from realtime_v2.worker_board_data_hub_patch import install as install_board_data_hub"
    )
    detail_install_index = entry.rindex("_install_theme_selected_detail_fail_open()")
    hub_call_index = entry.rindex("_install_board_data_hub_fail_open()")
    assert split_index < hub_import_index
    assert hub_call_index < detail_install_index

    detail_patch = (
        ROOT / "realtime_v2" / "worker_theme_selected_detail_patch.py"
    ).read_text(encoding="utf-8")
    assert 'projection_snapshot("theme_detail")' in detail_patch
    assert "HTTPStatus.ACCEPTED" in detail_patch
    assert '"http_calculation_allowed": False' in detail_patch
    assert "THEMEBOARD_SELECTED_DETAIL_LATEST_ONLY_20260713" in detail_patch
