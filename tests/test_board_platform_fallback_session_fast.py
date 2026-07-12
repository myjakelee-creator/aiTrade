from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.board_platform.previous_daily_fast import install as install_previous
from realtime_v2.board_platform.session_metric_fast import install as install_session


class State:
    def __init__(self):
        self.status = {}
        self.previous_daily_display_values_by_code = {
            "005930": {
                "strength_5m": 120,
                "strength_source": "previous",
            }
        }
        self.daily_values_by_code = {}
        self.session_metric_hold_by_code = {}
        self.session_metric_hold_dirty = False


def test_previous_daily_values_are_validated_once_and_reused():
    calls = {"usable": 0}

    def usable(_key, value):
        calls["usable"] += 1
        return value not in (None, "")

    module = SimpleNamespace(
        DISPLAY_FALLBACK_KEYS=("strength_5m", "strength_source"),
        _load_previous_daily_display_values_if_needed=lambda state: state.previous_daily_display_values_by_code,
        _fallback_value_is_usable=usable,
        _should_use_previous_daily_display=lambda session: True,
        normalize_code=lambda value: str(value).zfill(6),
        to_number=lambda value: float(value) if value not in (None, "") else None,
    )
    install_previous(module)
    state = State()

    first = {"stock_code": "005930", "strength_5m": None}
    module._apply_previous_daily_display_fallback(state, first, {"phase": "closed"})
    first_count = calls["usable"]

    second = {"stock_code": "005930", "strength_5m": None}
    module._apply_previous_daily_display_fallback(state, second, {"phase": "closed"})

    assert first["strength_5m"] == 120
    assert second["strength_5m"] == 120
    assert calls["usable"] == first_count
    assert state.status["previous_daily_fast_cache_count"] == 1


def _session_module():
    group_keys = {
        "orderbook": ("bid_ask_ratio", "orderbook_received_at"),
        "execution": ("execution_strength",),
        "strength5": ("strength_5m",),
        "program": ("program_net", "program_net_updated_at"),
        "large_trade": ("large_trade_net_count", "large_trade_updated_at"),
    }
    markers = {group: f"_{group}_date" for group in group_keys}

    module = SimpleNamespace(
        GROUP_KEYS=group_keys,
        GROUP_DATE_MARKER=markers,
        to_number=lambda value: float(value) if value not in (None, "") else None,
        _session_started=lambda session: bool(session.get("started")),
        _ensure_hold_cache=lambda state: state.session_metric_hold_by_code,
        now_text=lambda: "2026-07-12T10:00:00",
    )

    def group_usable(source, group):
        primary = {
            "orderbook": "bid_ask_ratio",
            "execution": "execution_strength",
            "strength5": "strength_5m",
            "program": "program_net",
            "large_trade": "large_trade_net_count",
        }[group]
        return source.get(primary) not in (None, "")

    def group_date(source, group):
        return source.get(markers[group]) or "20260712"

    def copy_group(target, source, group):
        copied = 0
        for key in group_keys[group]:
            if key in source and source[key] not in (None, ""):
                target[key] = source[key]
                copied += 1
        target[markers[group]] = group_date(source, group)
        return copied

    module._group_usable = group_usable
    module._group_date = group_date
    module._copy_group = copy_group
    return module


def test_unchanged_session_metric_row_skips_hold_update():
    module = _session_module()
    install_session(module)
    state = State()
    row = {
        "bid_ask_ratio": 1.2,
        "orderbook_received_at": "2026-07-12T10:00:00",
        "_orderbook_date": "20260712",
    }
    session = {"trading_date": "20260712", "started": True}

    assert module._update_hold_entry(state, "005930", row, session) is True
    assert module._update_hold_entry(state, "005930", row, session) is False
    assert state.status["session_metric_fast_skip_count"] == 1

    row["bid_ask_ratio"] = 1.4
    assert module._update_hold_entry(state, "005930", row, session) is True
    assert state.session_metric_hold_by_code["005930"]["bid_ask_ratio"] == 1.4


def test_fast_date_parser_handles_iso_and_compact_dates():
    module = _session_module()
    install_session(module)
    assert module._date_digits("2026-07-12T10:00:00") == "20260712"
    assert module._date_digits("20260712") == "20260712"
