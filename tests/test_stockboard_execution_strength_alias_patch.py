from __future__ import annotations

from realtime_v2.execution_strength_alias_patch import install


class DummyState:
    def __init__(self):
        self.applied = []

    def _apply_close_metrics(self, event):
        self.applied.append(event)


class DummyBase:
    State = DummyState

    @staticmethod
    def merged_event_values(event):
        values = {}
        if isinstance(event.get("values"), dict):
            values.update(event["values"])
        if isinstance(event.get("kwargs"), dict):
            values.update(event["kwargs"])
        return values


def test_realtime_strength_snapshot_is_exposed_as_execution_strength():
    install(DummyBase)
    state = DummyState()

    state._apply_close_metrics(
        {
            "type": "close_metrics",
            "ts": "2026-07-11T12:00:00",
            "stock_code": "000001",
            "values": {
                "realtime_strength_snapshot": 112.3,
                "strength_source": "opt10046",
            },
        }
    )

    values = state.applied[-1]["values"]
    assert values["execution_strength"] == 112.3
    assert values["last_valid_execution_strength"] == 112.3
    assert values["execution_strength_source"] == "opt10046"
