from __future__ import annotations

import ast
import threading
from copy import deepcopy
from pathlib import Path

from realtime_v2.worker_rest_live_metric_metadata_patch import install

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_rest_live_metric_metadata_patch.py"


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.daily_values_by_code = {
            "000001": {
                "orderbook_source": "ka10004_rest_lowload",
                "orderbook_status": "ok",
                "orderbook_received_at": "2026-07-15T16:35:00+09:00",
                "execution_strength_source": "ka10046_rest_lowload",
                "execution_strength_status": "ok",
                "execution_strength_updated_at": "2026-07-15T16:36:00+09:00",
            }
        }
        self.quotes = {
            "000001": {
                "stock_code": "000001",
                "bid_ask_ratio": 1.25,
                "execution_strength": 118.5,
            }
        }

    def rows(self, limit=300):
        return [
            {
                "stock_code": "000001",
                "bid_ask_ratio": 1.25,
                "execution_strength": 118.5,
            }
        ][:limit]


class FakeBase:
    State = FakeState


def test_missing_source_status_and_timestamps_are_restored_from_daily_state():
    install(FakeBase)
    state = FakeState()

    row = state.rows()[0]

    assert row["orderbook_source"] == "ka10004_rest_lowload"
    assert row["orderbook_status"] == "ok"
    assert row["orderbook_received_at"] == "2026-07-15T16:35:00+09:00"
    assert row["execution_strength_source"] == "ka10046_rest_lowload"
    assert row["execution_strength_status"] == "ok"
    assert row["execution_strength_updated_at"] == "2026-07-15T16:36:00+09:00"
    assert state.status["rest_metric_metadata_restored_rows"] == 1
    assert state.status["rest_metric_metadata_restored_fields"] == 6


def test_existing_row_metadata_is_not_overwritten():
    class ExistingState(FakeState):
        def rows(self, limit=300):
            return [
                {
                    "stock_code": "000001",
                    "bid_ask_ratio": 1.25,
                    "orderbook_source": "existing_source",
                    "execution_strength": 118.5,
                    "execution_strength_source": "existing_strength_source",
                }
            ][:limit]

    class ExistingBase:
        State = ExistingState

    install(ExistingBase)
    row = ExistingState().rows()[0]

    assert row["orderbook_source"] == "existing_source"
    assert row["execution_strength_source"] == "existing_strength_source"


def test_metadata_patch_has_no_network_qax_sort_or_browser_work():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "requests.",
        "urlopen(",
        "Thread(",
        "sorted(",
        "document.",
        "fetch(",
    ):
        assert forbidden not in source
