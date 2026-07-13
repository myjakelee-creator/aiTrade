from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import theme_projection_momentum_patch as patch


class DummyBuilder:
    def __init__(self):
        pass

    def __call__(self, feature_version, rows, meta):
        detail = {
            "theme_id": "T1",
            "theme_name": "테스트",
            "avg_change_rate": rows[0]["avg_change_rate"],
            "members": [
                {"change_rate": 1.0, "amount_ratio": 2.0},
                {"change_rate": 3.0, "amount_ratio": 4.0},
                {"change_rate": 9.0, "amount_ratio": 100.0},
            ],
        }
        return {
            "status": "READY",
            "rows": [{key: value for key, value in detail.items() if key != "members"}],
            "details": {"T1": detail},
            "policy": {},
            "calculate_ms": 0.0,
        }


def test_theme_momentum_uses_median_amount_ratio_and_light_history(monkeypatch, tmp_path):
    monkeypatch.setattr(patch, "MOMENTUM_HOLD_PATH", tmp_path / "momentum.json")
    monkeypatch.setattr(
        patch,
        "_session",
        lambda: {
            "phase": "regular",
            "accept_realtime": True,
            "is_trading_day": True,
            "trading_date": "20260714",
        },
    )

    module = SimpleNamespace(ThemeProjectionBuilder=DummyBuilder)
    patch.install(module)
    builder = module.ThemeProjectionBuilder()

    first = builder(1, ({"avg_change_rate": 1.0},), {"snapshot_epoch": 0.0, "trading_date": "20260714"})
    second = builder(2, ({"avg_change_rate": 2.0},), {"snapshot_epoch": 61.0, "trading_date": "20260714"})
    third = builder(3, ({"avg_change_rate": 4.0},), {"snapshot_epoch": 301.0, "trading_date": "20260714"})

    assert first["details"]["T1"]["median_change_rate"] == 3.0
    assert first["details"]["T1"]["theme_amount_ratio"] == 4.0
    assert first["details"]["T1"]["amount_ratio_member_count"] == 3
    assert second["details"]["T1"]["change_momentum_1m"] == 1.0
    assert third["details"]["T1"]["change_persistence_5m"] == 3.0
    assert third["trend_feature_status"]["history_theme_count"] == 1
    assert third["policy"]["additional_tr_allowed"] is False
    assert third["policy"]["browser_feature_calculation_allowed"] is False


def test_theme_momentum_contains_no_openapi_or_browser_calculation_path():
    source = Path("realtime_v2/theme_projection_momentum_patch.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "dynamicCall",
        "CommRqData",
        "SetRealReg",
        "QAxWidget",
        "kiwoom_data_provider",
    ):
        assert forbidden not in source
    assert "one_float_per_active_theme_per_second_310s" in source
    assert "median_positive_member_amount_ratio" in source
