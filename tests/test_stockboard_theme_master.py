import json
from pathlib import Path

from stockboard_theme_master import load_theme_master


def test_theme_master_filters_invalid_members(tmp_path: Path):
    path = tmp_path / "themes.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "themes": [
            {
                "theme_id": "GOOD",
                "theme_name": "Good",
                "members": [
                    {"stock_code": "005930", "role": "primary"},
                    {"stock_code": "005930", "role": "secondary"},
                    {"stock_code": "BAD"}
                ]
            },
            {
                "theme_id": "EMPTY",
                "theme_name": "Empty",
                "members": [{"stock_code": "000660"}]
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")

    master = load_theme_master(path, known_codes={"005930"})

    assert [theme.theme_id for theme in master.themes] == ["GOOD"]
    assert [member.stock_code for member in master.themes[0].members] == ["005930"]
    assert len(master.invalid_themes) == 1
    assert any("duplicate member ignored" in warning for warning in master.warnings)
    assert any("invalid stock_code" in warning for warning in master.warnings)


def test_membership_count_tracks_cross_theme_membership(tmp_path: Path):
    path = tmp_path / "themes.json"
    path.write_text(json.dumps({
        "themes": [
            {"theme_id": "A", "theme_name": "A", "members": [{"stock_code": "005930"}]},
            {"theme_id": "B", "theme_name": "B", "members": [{"stock_code": "005930"}, {"stock_code": "000660"}]}
        ]
    }), encoding="utf-8")
    master = load_theme_master(path)
    assert master.membership_count_by_code == {"005930": 2, "000660": 1}
