from __future__ import annotations

import ast
import json
from pathlib import Path

from scripts import stockboard_v2_price_history_backtrace as backtrace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stockboard_v2_price_history_backtrace.py"
CMD_PATH = ROOT / "stockboard_v2_large.cmd"


def _trade_event(ts: str, code: str, price: int, rate: float) -> dict:
    return {
        "type": "trade",
        "ts": ts,
        "stock_code": code,
        "kwargs": {
            "price": price,
            "change_rate": rate,
            "trade_time": ts[11:19].replace(":", ""),
            "registered_code": f"{code}_AL",
        },
    }


def _row(**overrides):
    row = {
        "local_rank": 1,
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "local_price": 263500,
        "rest_price": 264000,
        "local_change_rate": 1.74,
        "rest_change_rate": 1.93,
        "local_received_at": "2026-07-22T14:54:30+09:00",
    }
    row.update(overrides)
    return row


def test_backtrace_classifies_collector_output_gap(tmp_path):
    target = backtrace._timestamp("2026-07-22T14:57:18+09:00")
    assert target is not None
    event_path = tmp_path / "events_20260722.jsonl"
    event_path.write_text(
        json.dumps(
            _trade_event("2026-07-22T14:54:30+09:00", "005930", 263500, 1.74),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    states = backtrace._scan_events(
        event_path, {"005930"}, target, lookback_sec=300, lookahead_sec=60
    )
    result = backtrace._row_result(
        _row(), states["005930"], target, stale_sec=5
    )

    assert result["state"] == "COLLECTOR_OUTPUT_GAP_AT_TARGET"
    assert result["last_event_age_at_target_sec"] == 168.0


def test_backtrace_infers_worker_or_guard_gap_when_signed_event_matches_kiwoom(tmp_path):
    target = backtrace._timestamp("2026-07-22T14:57:18+09:00")
    assert target is not None
    event_path = tmp_path / "events_20260722.jsonl"
    event_path.write_text(
        json.dumps(
            _trade_event("2026-07-22T14:57:17.700+09:00", "005930", -264000, 1.93),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    states = backtrace._scan_events(
        event_path, {"005930"}, target, lookback_sec=300, lookahead_sec=60
    )
    result = backtrace._row_result(
        _row(), states["005930"], target, stale_sec=5
    )

    assert result["state"] == "WORKER_OR_GUARD_NOT_APPLIED"
    assert result["inference_only"] is True
    assert result["event_price"] == 264000


def test_backtrace_marks_full_match_when_event_local_and_kiwoom_agree(tmp_path):
    target = backtrace._timestamp("2026-07-22T14:57:18+09:00")
    assert target is not None
    event_path = tmp_path / "events_20260722.jsonl"
    event_path.write_text(
        json.dumps(
            _trade_event("2026-07-22T14:57:17.900+09:00", "005930", 264000, 1.93),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    states = backtrace._scan_events(
        event_path, {"005930"}, target, lookback_sec=300, lookahead_sec=60
    )
    result = backtrace._row_result(
        _row(
            local_price=264000,
            local_change_rate=1.93,
            local_received_at="2026-07-22T14:57:17.900+09:00",
        ),
        states["005930"],
        target,
        stale_sec=5,
    )

    assert result["state"] == "PATH_MATCHED_AT_TARGET"
    assert result["inference_only"] is False


def test_history_backtrace_is_file_only_and_launcher_does_not_restart():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "events_" in source
    assert "price_compare_" in source
    assert "guard_decision_directly_logged" in source
    assert "_normalized_price" in source
    for forbidden in (
        "urlopen",
        "QAxWidget",
        "SetRealReg",
        "dynamicCall",
        "CommRqData",
        "issue_access_token",
        "fetch_trade_value_top100",
        "websocket",
    ):
        assert forbidden not in source

    launcher = CMD_PATH.read_text(encoding="utf-8-sig")
    assert 'if /I "%ACTION%"=="price-backtrace"' in launcher
    block = launcher.split('if /I "%ACTION%"=="price-backtrace"', 1)[1].split(
        "goto direct_action", 1
    )[0]
    assert "restart" not in block.lower()
    assert '"%SAFE%"' not in block
    assert "PREFLIGHT" not in block
