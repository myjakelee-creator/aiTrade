from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from realtime_v2.board_data_hub import BoardDataHub, StaleProjectionInput


def test_hub_versions_canonical_feature_and_projection_contract():
    hub = BoardDataHub()
    state = SimpleNamespace(
        lock=threading.RLock(),
        status={"event_count": 1, "universe_count": 2},
        quotes={
            "000001": {
                "stock_code": "000001",
                "seed_rank": 2,
                "trade_value_eok": 10,
                "price": 100,
            },
            "000002": {
                "stock_code": "000002",
                "seed_rank": 1,
                "trade_value_eok": 20,
                "price": 200,
            },
        },
    )

    state_version = hub.mark_state_change(
        event_type="trade",
        at="2026-07-13T09:00:00+09:00",
        stock_code="000002_AL",
    )
    assert state_version == 1

    canonical = hub.canonical_snapshot(state, limit=2)
    assert canonical["state_version"] == 1
    assert [row["stock_code"] for row in canonical["rows"]] == ["000002", "000001"]

    feature_version = hub.publish_feature_snapshot(
        {
            "schema_version": 1,
            "source": "candidate-engine",
            "status": {"candidate_model_id": "model-a"},
            "rows": [
                {"stock_code": "000002", "candidate_score": 90},
                {"stock_code": "000001", "candidate_score": 80},
            ],
        }
    )
    assert feature_version == 1

    features = hub.feature_snapshot(limit=1)
    assert features["feature_version"] == 1
    assert features["candidate_version"] == 1
    assert features["input_state_version"] == 1
    assert features["rows"] == [{"stock_code": "000002", "candidate_score": 90}]

    projection_version = hub.publish_projection(
        "theme",
        {"themes": [{"name": "반도체", "score": 88}]},
        input_feature_version=feature_version,
    )
    assert projection_version == 1
    projection = hub.projection_snapshot("theme")
    assert projection["input_feature_version"] == 1
    assert projection["payload"]["themes"][0]["name"] == "반도체"

    with pytest.raises(StaleProjectionInput):
        hub.publish_projection(
            "strategy",
            {"signals": []},
            input_feature_version=0,
        )


def test_hub_readers_receive_copies_and_policy_forbids_direct_board_tr():
    hub = BoardDataHub()
    source_row = {"stock_code": "000001", "nested": {"score": 70}}
    hub.publish_feature_snapshot({"rows": [source_row]})

    first = hub.feature_snapshot(limit=1)
    first["rows"][0]["nested"]["score"] = 1
    second = hub.feature_snapshot(limit=1)
    assert second["rows"][0]["nested"]["score"] == 70

    manifest = hub.manifest()
    assert manifest["policy"]["direct_board_tr_allowed"] is False
    assert manifest["policy"]["direct_board_feature_calculation_allowed"] is False
    assert manifest["policy"]["html_calculation_allowed"] is False
