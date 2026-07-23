from __future__ import annotations

from types import SimpleNamespace

from realtime_v2 import closed_server_metric_completion_patch as patch


class _State:
    def __init__(self):
        self.lock = __import__("threading").RLock()
        self.status = {}

    def rows(self, _limit=300):
        return [
            {
                "stock_code": "005930",
                "trade_value_eok": 77986.08,
                "prev_trade_value_eok": 107886.78,
                "prev_rank": 2,
                "candidate_grade": "A99",
                "one_min_trade_value_eok": 0.0,
            }
        ]


def test_closed_rows_complete_ratio_alias_grade_and_hide_unproven_zero(monkeypatch):
    monkeypatch.setattr(
        patch,
        "market_session_now",
        lambda: SimpleNamespace(phase="closed"),
    )
    patch.install(SimpleNamespace(State=_State))
    state = _State()

    row = state.rows(300)[0]

    assert row["amount_ratio"] == round(77986.08 / 107886.78, 4)
    assert row["amount_ratio_status"] == "closed_server_completed"
    assert row["prev_rank"] == 2
    assert row["grade"] == "A99"
    assert "one_min_trade_value_eok" not in row
    assert row["one_min_status"] == "unavailable_after_restart_without_completed_bucket"
    assert state.status["closed_server_metric_completion_installed"] is True


def test_active_session_is_unchanged(monkeypatch):
    class ActiveState:
        def __init__(self):
            self.lock = __import__("threading").RLock()
            self.status = {}

        def rows(self, _limit=300):
            return [
                {
                    "trade_value_eok": 100.0,
                    "prev_trade_value_eok": 50.0,
                    "one_min_trade_value_eok": 0.0,
                }
            ]

    monkeypatch.setattr(
        patch,
        "market_session_now",
        lambda: SimpleNamespace(phase="regular"),
    )
    patch.install(SimpleNamespace(State=ActiveState))
    row = ActiveState().rows(300)[0]
    assert "amount_ratio" not in row
    assert row["one_min_trade_value_eok"] == 0.0


def test_runtime_wrapper_installs_on_worker_state_chain(monkeypatch):
    from realtime_v2 import worker_opening_burst_cache_patch as opening

    class RuntimeState:
        def __init__(self):
            self.lock = __import__("threading").RLock()
            self.status = {}

        def rows(self, _limit=300):
            return [{"trade_value_eok": 20.0, "prev_trade_value_eok": 10.0}]

    base = SimpleNamespace(State=RuntimeState)
    calls = []

    def original_install(received_base):
        calls.append(received_base)

    monkeypatch.setattr(opening, "install", original_install)
    monkeypatch.delattr(
        opening,
        "_closed_server_metric_completion_install_wrapped",
        raising=False,
    )
    monkeypatch.setattr(
        patch,
        "market_session_now",
        lambda: SimpleNamespace(phase="closed"),
    )

    patch.install_runtime_wrapper()
    opening.install(base)

    assert calls == [base]
    row = RuntimeState().rows(300)[0]
    assert row["amount_ratio"] == 2.0
