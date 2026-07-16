from __future__ import annotations

import json
from pathlib import Path

from realtime_v2.worker_approved_minute_pipeline import (
    LARGE_THRESHOLD_DEFAULT,
    parse_orderbook_events,
    parse_trade_events,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "stockboard_live_metrics_rest.json"
PIPELINE = ROOT / "realtime_v2" / "worker_approved_minute_pipeline.py"
HTML_PATCH = ROOT / "realtime_v2" / "html_approved_minute_metrics_patch.py"
COLLECTOR = ROOT / "realtime_v2" / "collector32_large_bidask.py"


def test_parse_trade_event_keeps_raw_signed_quantity_before_ui_coalescing():
    events = parse_trade_events(
        {
            "trnm": "REAL",
            "data": [
                {
                    "type": "0B",
                    "item": "000660_AL",
                    "values": {
                        "20": "090001",
                        "10": "+2067000",
                        "15": "+30",
                        "13": "120030",
                        "14": "100500",
                        "228": "105.25",
                        "9081": "3",
                        "290": "3",
                    },
                }
            ],
        }
    )

    assert events == [
        {
            "stock_code": "000660",
            "execution_strength": 105.25,
            "execution_strength_source_time": "090001",
            "execution_strength_exchange": "3",
            "execution_strength_market_phase": "3",
            "execution_strength_trade_price": 2067000.0,
            "signed_trade_qty": 30,
            "cumulative_volume": 120030,
            "cumulative_trade_value_raw": 100500.0,
            "raw_item": "000660_AL",
        }
    ]
    assert events[0]["execution_strength_trade_price"] * abs(events[0]["signed_trade_qty"]) >= LARGE_THRESHOLD_DEFAULT


def test_parse_rotating_orderbook_uses_total_bid_and_ask_fields():
    events = parse_orderbook_events(
        {
            "trnm": "REAL",
            "data": [
                {
                    "type": "0D",
                    "item": "005930_AL",
                    "values": {
                        "121": "-200000",
                        "125": "+300000",
                        "27": "+85000",
                        "28": "+84900",
                    },
                }
            ],
        },
        {
            "type": "0D",
            "total_ask_field": "121",
            "total_bid_field": "125",
            "best_ask_field": "27",
            "best_bid_field": "28",
        },
    )

    assert len(events) == 1
    event = events[0]
    assert event["stock_code"] == "005930"
    assert event["ask_volume"] == 200000
    assert event["bid_volume"] == 300000
    assert event["bid_ask_ratio"] == 1.5
    assert event["bid_pct"] == 60
    assert event["ask_pct"] == 40


def test_config_matches_approved_cadence_and_keeps_price_collector_unchanged():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    regular = config["session_manager"]["phase_policies"]["regular"]
    opening = config["session_manager"]["phase_policies"]["opening_burst"]
    orderbook = config["realtime_orderbook_ws"]
    strength_ws = config["realtime_strength_ws"]

    assert config["schema_version"] == 11
    assert orderbook["rotation_symbols"] == 20
    assert orderbook["rotation_sec"] == 12
    assert orderbook["ui_publish_sec"] == 60
    assert strength_ws["max_symbols"] == 100
    assert strength_ws["ui_publish_sec"] == 60
    assert regular["min_request_gap_sec"] == 3.0
    assert regular["intervals"]["strength"] == {
        "s1": 300,
        "top20": 300,
        "top100": 300,
    }
    assert opening["intervals"]["strength"]["top100"] == 300
    assert regular["intervals"]["bidask"]["top100"] == 0
    assert regular["intervals"]["large_trade"]["top100"] == 0
    assert config["approved_minute_pipeline"]["strength_open_delay_minutes"] == 5

    collector_source = COLLECTOR.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source
    assert "large_trade_enabled=False" in collector_source


def test_pipeline_source_counts_large_trades_before_latest_value_publish():
    source = PIPELINE.read_text(encoding="utf-8")
    stage_index = source.index("self.state.stage_approved_trade_events(trade_events)")
    latest_status_index = source.index("realtime_strength_ws_last_value=latest.get")
    assert stage_index < latest_status_index
    assert 'values.get("15")' in source
    assert "amount_krw = abs(price * signed_qty)" in source
    assert "publish_minute(self)" in source


def test_minute_trade_value_ui_is_compact_and_colored_at_100_percent():
    source = HTML_PATCH.read_text(encoding="utf-8")
    assert "label:'1분대금'" in source
    assert "`${minuteValue.toFixed(1)} ${minutePctText}`" in source
    assert "minutePct>=100?'plus':'minus'" in source
    assert "최근 완료 1분" in source
