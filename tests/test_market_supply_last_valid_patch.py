from pathlib import Path
from types import SimpleNamespace

from realtime_v2.market_supply_last_valid_patch import (
    _context_module,
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


def test_normalize_accepts_uppercase_market_keys_and_alias_fields():
    normalized = normalize_market_supply(
        {
            "KOSPI": {
                "index": "2,800.25",
                "change_rate": "1.2",
                "advance": "700",
                "decline": "200",
                "individual": "10",
                "foreign": "-20",
                "institution": "30",
                "program": "40",
            },
            "KOSDAQ": {
                "지수": "900.5",
                "등락률": "2.3",
                "상승": "1000",
                "하락": "300",
            },
        }
    )

    assert normalized["kospi"]["market_index"] == "2,800.25"
    assert normalized["kospi"]["program_market_eok"] == "40"
    assert normalized["kosdaq"]["market_change_rate"] == "2.3"
    assert market_supply_valid(normalized)


def test_normalize_accepts_nested_values_and_row_list_shapes():
    nested = normalize_market_supply(
        {
            "values": {
                "markets": [
                    {
                        "market_name": "KOSPI",
                        "cur_prc": 2801,
                        "flu_rt": 0.4,
                        "rising": 600,
                        "fall": 300,
                    },
                    {
                        "market_name": "KOSDAQ",
                        "cur_prc": 901,
                        "flu_rt": 0.8,
                        "rising": 900,
                        "fall": 400,
                    },
                ]
            }
        }
    )

    assert nested["kospi"]["market_index"] == 2801
    assert nested["kosdaq"]["advancers"] == 900
    assert market_supply_valid(nested)


def test_context_module_resolves_actual_guarded_provider_from_wrapper():
    guarded = SimpleNamespace(_runtime_context_payload=lambda: {})
    wrapper = SimpleNamespace(guarded=guarded)
    assert _context_module(wrapper) is guarded


def test_worker_installs_market_supply_hold_before_server_main():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "worker64_guarded_large_bidask.py"
    ).read_text(encoding="utf-8")
    assert "_install_market_supply_hold_fail_open()" in source
    assert "market_supply_last_valid_patch_error.txt" in source
