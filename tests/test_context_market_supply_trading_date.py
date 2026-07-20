from __future__ import annotations

from datetime import datetime

from realtime_v2 import context_snapshot_writer_singleflight as writer


def test_holiday_targets_last_completed_trading_date():
    target_date, phase = writer.market_supply_target_context(
        datetime(2026, 7, 17, 22, 30, 0)
    )

    assert phase == "holiday"
    assert target_date == "20260716"


def test_regular_session_targets_current_trading_date():
    target_date, phase = writer.market_supply_target_context(
        datetime(2026, 7, 16, 10, 0, 0)
    )

    assert phase == "regular"
    assert target_date == "20260716"


def test_physical_fetch_uses_target_date_and_restores_base_function(monkeypatch):
    observed = {}
    original_date_function = writer.base.trading_date_text

    def fake_fetch():
        observed["query_date"] = writer.base.trading_date_text()
        return {
            "source": "fake_market_supply",
            "kospi": {},
            "kosdaq": {},
        }

    monkeypatch.setattr(writer, "_original_fetch_live_market_supply_snapshot", fake_fetch)

    payload = writer._physical_market_supply_fetch("20260716", "holiday")

    assert observed["query_date"] == "20260716"
    assert writer.base.trading_date_text is original_date_function
    assert payload["source_trading_date"] == "20260716"
    assert payload["trading_date"] == "20260716"
    assert payload["target_date"] == "20260716"
    assert payload["query_date"] == "20260716"
    assert payload["market_phase"] == "holiday"
    assert (
        payload["market_supply_date_policy_version"]
        == "market_supply_actual_trading_date_v1"
    )


def test_singleflight_cache_key_and_payload_use_same_target_date(monkeypatch):
    observed = {}

    class FakeCoordinator:
        def execute(self, **kwargs):
            observed.update({key: value for key, value in kwargs.items() if key != "fetcher"})
            return kwargs["fetcher"]()

        def status(self):
            return {}

    monkeypatch.setattr(writer, "coordinator", FakeCoordinator())
    monkeypatch.setattr(
        writer,
        "market_supply_target_context",
        lambda now=None: ("20260716", "holiday"),
    )
    monkeypatch.setattr(
        writer,
        "_physical_market_supply_fetch",
        lambda target_date, phase: {
            "source_trading_date": target_date,
            "market_phase": phase,
            "kospi": {},
            "kosdaq": {},
        },
    )

    payload = writer.fetch_live_market_supply_snapshot()

    assert observed["trading_date"] == "20260716"
    assert observed["market_session"] == "holiday"
    assert observed["params"]["target_trading_date"] == "20260716"
    assert payload["source_trading_date"] == "20260716"
    assert payload["query_date"] == "20260716"
    assert payload["market_phase"] == "holiday"
