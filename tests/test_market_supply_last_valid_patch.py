from pathlib import Path

from realtime_v2.market_supply_last_valid_patch import market_supply_valid


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


def test_worker_installs_market_supply_hold_before_server_main():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "worker64_guarded_large_bidask.py"
    ).read_text(encoding="utf-8")
    assert "_install_market_supply_hold_fail_open()" in source
    assert "market_supply_last_valid_patch_error.txt" in source
