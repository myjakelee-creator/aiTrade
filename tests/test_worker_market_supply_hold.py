from __future__ import annotations

import inspect
import json
from datetime import datetime
from pathlib import Path

from realtime_v2 import worker_market_supply_hold_patch as hold

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return {
        "enabled": True,
        "candidate_paths": ["candidate_market_supply.json"],
        "required_markets": ["kospi", "kosdaq"],
        "persist_filename": "market_supply_last_good_{trading_date}.json",
        "allow_previous_until_current_valid": True,
        "replace_only_with_valid_current_day": True,
    }


def _row(index: float, change: float, *, scale: int = 1) -> dict:
    return {
        "market_index": index,
        "market_change_rate": change,
        "advancers": 384 * scale,
        "decliners": 488 * scale,
        "upper_limit_count": 6,
        "lower_limit_count": 0,
        "individual_eok": -50624 * scale,
        "foreign_spot_eok": -20698 * scale,
        "institution_eok": -31684 * scale,
        "program_market_eok": -19187 * scale,
    }


def _supply(trading_date: str, *, scale: int = 1) -> dict:
    return {
        "trading_date": trading_date,
        "kospi": _row(6820.6, -6.37, scale=scale),
        "kosdaq": _row(791.84, -4.53, scale=scale),
    }


def _reset_payload(trading_date: str) -> dict:
    return {
        "trading_date": trading_date,
        "kospi": {
            "individual_eok": 0,
            "foreign_spot_eok": 0,
            "institution_eok": 0,
            "program_market_eok": 0,
        },
        "kosdaq": {
            "individual_eok": 0,
            "foreign_spot_eok": 0,
            "institution_eok": 0,
            "program_market_eok": 0,
        },
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_market_supply_validation_accepts_real_zero_supply_and_rejects_close_reset():
    zero_supply = _supply("20260720")
    for market in ("kospi", "kosdaq"):
        zero_supply[market]["individual_eok"] = 0
        zero_supply[market]["foreign_spot_eok"] = 0
        zero_supply[market]["institution_eok"] = 0
        zero_supply[market]["program_market_eok"] = 0

    assert hold.validate_market_supply(zero_supply) == (True, None)

    valid, reason = hold.validate_market_supply(_reset_payload("20260720"))
    assert valid is False
    assert reason == "missing_or_invalid_index:kospi"


def test_after_close_rejects_reset_and_keeps_same_day_last_good(tmp_path):
    candidate = tmp_path / "candidate_market_supply.json"
    runtime = tmp_path / "runtime"
    manager = hold.MarketSupplyHold(_config(), root=tmp_path, runtime_dir=runtime)

    normal = _supply("20260720")
    _write(candidate, normal)
    displayed, status = manager.resolve({}, now=datetime(2026, 7, 20, 19, 59, 0))
    assert displayed == normal
    assert status["source_trading_date"] == "20260720"
    assert (runtime / "market_supply_last_good_20260720.json").is_file()

    _write(candidate, _reset_payload("20260720"))
    held, closed_status = manager.resolve({}, now=datetime(2026, 7, 20, 20, 1, 0))

    assert held == normal
    assert closed_status["display_basis"] == "after_close_hold"
    assert closed_status["candidate_valid"] is False
    assert closed_status["candidate_reject_reason"] == "missing_or_invalid_index:kospi"


def test_premarket_keeps_previous_until_first_valid_current_day_snapshot(tmp_path):
    candidate = tmp_path / "candidate_market_supply.json"
    runtime = tmp_path / "runtime"

    previous = _supply("20260720")
    _write(candidate, previous)
    first = hold.MarketSupplyHold(_config(), root=tmp_path, runtime_dir=runtime)
    first.resolve({}, now=datetime(2026, 7, 20, 19, 59, 0))

    restarted = hold.MarketSupplyHold(_config(), root=tmp_path, runtime_dir=runtime)
    held, held_status = restarted.resolve({}, now=datetime(2026, 7, 21, 8, 5, 0))
    assert held == previous
    assert held_status["display_basis"] == "premarket_previous_hold"
    assert held_status["expected_trading_date"] == "20260721"
    assert held_status["source_trading_date"] == "20260720"

    current = _supply("20260721", scale=2)
    _write(candidate, current)
    replaced, live_status = restarted.resolve({}, now=datetime(2026, 7, 21, 8, 6, 0))
    assert replaced == current
    assert live_status["display_basis"] == "live"
    assert live_status["source_trading_date"] == "20260721"
    assert (runtime / "market_supply_last_good_20260721.json").is_file()


def test_closed_restart_loads_persisted_same_day_value(tmp_path):
    candidate = tmp_path / "candidate_market_supply.json"
    runtime = tmp_path / "runtime"
    normal = _supply("20260720")

    _write(candidate, normal)
    hold.MarketSupplyHold(_config(), root=tmp_path, runtime_dir=runtime).resolve(
        {}, now=datetime(2026, 7, 20, 19, 58, 0)
    )
    _write(candidate, _reset_payload("20260720"))

    restarted = hold.MarketSupplyHold(_config(), root=tmp_path, runtime_dir=runtime)
    displayed, status = restarted.resolve({}, now=datetime(2026, 7, 20, 21, 0, 0))
    assert displayed == normal
    assert status["display_basis"] == "after_close_hold"
    assert status["source_trading_date"] == "20260720"


def test_market_supply_hold_config_and_production_install_are_locked():
    config = hold._load_config()
    assert config["enabled"] is True
    assert config["required_markets"] == ["kospi", "kosdaq"]
    assert config["allow_previous_until_current_valid"] is True
    assert config["replace_only_with_valid_current_day"] is True

    source = (ROOT / "realtime_v2" / "worker_tr_singleflight_patch.py").read_text(
        encoding="utf-8"
    )
    assert "from realtime_v2.worker_market_supply_hold_patch import" in source
    assert "install_market_supply_hold()" in source
    assert source.index("install_momentum_badge_policy(base)") < source.index(
        "install_market_supply_hold()"
    )


def test_market_supply_hold_adds_no_request_thread_timer_or_browser_path():
    source = inspect.getsource(hold)
    assert "urllib" not in source
    assert "requests" not in source
    assert "WebSocket(" not in source
    assert "new EventSource" not in source
    assert "Thread(" not in source
    assert "setInterval(" not in source
    assert "setTimeout(" not in source
    assert "_runtime_context_payload" in source
