from pathlib import Path

from realtime_v2.market_supply_last_valid_patch import (
    market_supply_valid,
    normalize_market_supply,
)


def _valid_entry(index: float) -> dict:
    return {
        "market_index": index,
        "market_change_rate": 1.23,
        "advancers": 100,
        "decliners": 50,
    }


def test_market_supply_valid_requires_both_markets():
    assert market_supply_valid(
        {
            "kospi": _valid_entry(2800.0),
            "kosdaq": _valid_entry(900.0),
        }
    )
    assert not market_supply_valid({"kospi": _valid_entry(2800.0)})


def test_market_supply_valid_rejects_blank_weekend_payload():
    assert not market_supply_valid(
        {
            "kospi": {
                "market_index": None,
                "market_change_rate": None,
                "advancers": 0,
                "decliners": 0,
            },
            "kosdaq": {
                "market_index": None,
                "market_change_rate": None,
                "advancers": 0,
                "decliners": 0,
            },
        }
    )


def test_normalize_supports_uppercase_and_wrapped_values():
    normalized = normalize_market_supply(
        {
            "values": {
                "KOSPI": {
                    "index": "2800.1",
                    "change_rate": "1.2",
                    "advance": "100",
                    "decline": "50",
                },
                "KOSDAQ": {
                    "index": "900.2",
                    "change_rate": "-0.3",
                    "advance": "80",
                    "decline": "70",
                },
            }
        }
    )
    assert normalized["kospi"]["market_index"] == "2800.1"
    assert normalized["kosdaq"]["decliners"] == "70"
    assert market_supply_valid(normalized)


def test_worker_installs_market_supply_hold_on_actual_context_module():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "worker64_guarded_large_bidask.py"
    ).read_text(encoding="utf-8")
    assert "_install_market_supply_hold_fail_open()" in source
    assert 'context_module = getattr(large, "guarded", large)' in source
    assert "market_supply_last_valid_patch_error.txt" in source


def test_context_patch_is_fail_open():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "market_supply_last_valid_patch.py"
    ).read_text(encoding="utf-8")
    assert '"market_supply_display_basis": "ORIGINAL_CONTEXT_ERROR"' in source
    assert 'payload["market_supply_display_basis"] = "PATCH_FAIL_OPEN"' in source
    assert "market_supply_patch_traceback" in source
