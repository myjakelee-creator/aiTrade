from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from realtime_v2.board_platform.display_hold_fast import install as install_hold_fast
from realtime_v2.board_platform.market_session_cache import install as install_session_cache


class FakeState:
    def __init__(self):
        self.daily_values_by_code = {
            "005930": {
                "price": 100,
                "change_rate": 1.5,
                "received_at": "2026-07-13T09:00:00",
            }
        }
        self.display_hold_by_code = {
            "005930": {
                "stock_code": "005930",
                "trading_date": "20260713",
                "price": 90,
                "change_rate": 0.5,
                "updated_at": "2026-07-13T08:59:00",
            }
        }
        self.previous = {
            "005930": {
                "stock_code": "005930",
                "trading_date": "20260710",
                "price": 80,
                "change_rate": -1.0,
                "updated_at": "2026-07-10T15:30:00",
            }
        }
        self.status = {}
        self.display_hold_dirty = False
        self.display_hold_last_save = 0.0
        self.mark_dirty_count = 0

    def _mark_daily_dirty(self):
        self.mark_dirty_count += 1


def _fake_hold():
    calls = {"entry": 0, "write": 0}

    def number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def positive(value):
        value = number(value)
        return value if value is not None and value > 0 else None

    def usable(key, value):
        if key == "price":
            return positive(value)
        if key == "change_rate":
            return number(value)
        return value

    def entry_from_source(code, source, origin):
        calls["entry"] += 1
        result = {
            "stock_code": code,
            "trading_date": "20260713",
            "display_hold_origin": origin,
            "updated_at": source.get("received_at") or source.get("updated_at"),
        }
        for key in ("price", "change_rate"):
            value = usable(key, source.get(key))
            if value is not None:
                result[key] = value
        return result

    def atomic_write_json(path, payload):
        calls["write"] += 1
        calls["payload"] = payload

    module = SimpleNamespace(
        DISPLAY_HOLD_KEYS=("price", "change_rate"),
        POSITIVE_NUMBER_KEYS={"price"},
        PRESENT_NUMBER_KEYS={"change_rate"},
        COUNTER_KEYS=set(),
        DISPLAY_HOLD_SAVE_INTERVAL_SEC=5.0,
        DISPLAY_HOLD_PATH=Path("display_hold_last.json"),
        normalize_code=lambda value: str(value).zfill(6),
        trading_date_text=lambda: "20260713",
        now_text=lambda: "2026-07-13T09:00:01",
        _number=number,
        _positive=positive,
        _usable_value=usable,
        _entry_from_source=entry_from_source,
        _entry_date=lambda entry: str(entry.get("trading_date") or ""),
        _ensure_cache=lambda state: state.display_hold_by_code,
        _load_previous_daily_entries=lambda state: state.previous,
        atomic_write_json=atomic_write_json,
    )
    module.calls = calls
    return module


def test_hold_fast_preserves_priority_and_caches_current_entry(monkeypatch):
    hold = _fake_hold()
    install_hold_fast(hold)
    state = FakeState()

    first = {"stock_code": "005930", "price": None, "change_rate": None}
    assert hold._apply_hold_to_row(state, first, True) == 2
    assert first["price"] == 100
    assert first["change_rate"] == 1.5
    assert hold.calls["entry"] == 1

    second = {"stock_code": "005930", "price": 999, "change_rate": None}
    hold._apply_hold_to_row(state, second, True)
    assert second["price"] == 999
    assert second["change_rate"] == 1.5
    assert hold.calls["entry"] == 1

    state.daily_values_by_code["005930"].update(
        {"price": 110, "received_at": "2026-07-13T09:00:02"}
    )
    third = {"stock_code": "005930", "price": None, "change_rate": None}
    hold._apply_hold_to_row(state, third, True)
    assert third["price"] == 110
    assert hold.calls["entry"] == 2


def test_hold_capture_is_throttled_and_file_write_is_deferred(monkeypatch):
    monkeypatch.setenv("STOCKBOARD_DISPLAY_HOLD_CAPTURE_SEC", "10")
    hold = _fake_hold()
    install_hold_fast(hold)
    state = FakeState()
    quote = {
        "stock_code": "005930",
        "price": 120,
        "change_rate": 2.0,
        "received_at": "2026-07-13T09:00:03",
    }

    hold._remember_from_quote(state, "005930", quote, "trade")
    hold._remember_from_quote(state, "005930", quote, "trade")

    assert state.status["display_hold_capture_count"] == 1
    assert state.status["display_hold_capture_skip_count"] == 1
    assert hold.calls["write"] == 0

    hold._write_cache_if_needed(state, force=True)
    assert hold.calls["write"] == 1
    assert hold.calls["payload"]["values"]["005930"]["price"] == 120


def test_market_session_config_reuses_cached_payload(tmp_path):
    config_path = tmp_path / "calendar.json"
    config_path.write_text("{}", encoding="utf-8")
    calls = {"read": 0}

    def original_load():
        calls["read"] += 1
        return {"version": calls["read"]}

    module = SimpleNamespace(CONFIG_PATH=config_path, _load_config=original_load)
    install_session_cache(module, check_interval_sec=5.0)

    first = module._load_config()
    second = module._load_config()

    assert first is second
    assert first["version"] == 1
    assert calls["read"] == 1
    assert module._market_session_config_cache_hits == 1
