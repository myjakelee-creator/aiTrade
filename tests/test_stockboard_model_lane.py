from __future__ import annotations

from pathlib import Path

from realtime_v2.board_platform.model_lane import StockBoardModelLaneService


def _fake_engine(rows, model_id=None):
    enriched = []
    for row in rows:
        item = dict(row)
        score = 90 if item["stock_code"] == "000660" else 70
        item.update(
            {
                "candidate_model_id": model_id or "MODEL_DEFAULT",
                "candidate_score": score,
                "grade_score": score,
                "candidate_status": "READY" if score >= 80 else "WATCH",
                "candidate_grade": "A" if score >= 80 else "C",
                "score_breakdown": {"total": {"score": score}},
                "model_rank": 1 if score >= 80 else 2,
                "is_candidate": score >= 80,
                "desired_top20": True,
                "_source_rank": item.get("rank"),
                "trade_value_rank": item.get("rank"),
            }
        )
        enriched.append(item)
    return sorted(enriched, key=lambda row: row["model_rank"])


def _rows(price_a=100, price_b=200):
    return [
        {
            "stock_code": "005930",
            "price": price_a,
            "rank": 1,
            "rank_change": 3,
            "amount_ratio": 2.1,
        },
        {
            "stock_code": "000660",
            "price": price_b,
            "rank": 2,
            "rank_change": 1,
            "amount_ratio": 1.7,
        },
    ]


def test_model_lane_reuses_model_fields_without_overwriting_fast_fields():
    service = StockBoardModelLaneService(_fake_engine, interval_sec=1.0)
    service.submit(_rows(), "MODEL_A")
    assert service.compute_once(force=True) is True

    current = _rows(price_a=111, price_b=222)
    current[0]["rank"] = 2
    current[1]["rank"] = 1
    current[0]["trade_value_rank"] = 2
    current[1]["trade_value_rank"] = 1
    result = service.apply(current, "MODEL_A")

    assert [row["stock_code"] for row in result] == ["000660", "005930"]
    by_code = {row["stock_code"]: row for row in result}
    assert by_code["000660"]["price"] == 222
    assert by_code["000660"]["rank"] == 1
    assert by_code["000660"]["trade_value_rank"] == 1
    assert by_code["000660"]["candidate_score"] == 90
    assert by_code["005930"]["price"] == 111
    assert by_code["005930"]["rank"] == 2
    assert by_code["005930"]["trade_value_rank"] == 2
    assert "_source_rank" not in by_code["005930"]


def test_model_change_does_not_reuse_previous_model_fields():
    service = StockBoardModelLaneService(_fake_engine)
    service.submit(_rows(), "MODEL_A")
    service.compute_once(force=True)

    pending = service.apply(_rows(), "MODEL_B")

    assert all(row["candidate_model_id"] == "MODEL_B" for row in pending)
    assert all(row["candidate_score"] is None for row in pending)
    assert all(row["candidate_status"] == "MODEL_PENDING" for row in pending)
    assert all(row["model_validation_status"] == "PENDING" for row in pending)
    assert all(row["is_candidate"] is False for row in pending)


def test_model_lane_coalesces_latest_submissions():
    service = StockBoardModelLaneService(_fake_engine)
    service.submit(_rows(price_a=100), "MODEL_A")
    service.submit(_rows(price_a=110), "MODEL_A")
    service.submit(_rows(price_a=120), "MODEL_A")

    assert service.compute_once(force=True) is True

    status = service.status()
    assert status["submit_count"] == 3
    assert status["compute_count"] == 1
    assert status["coalesced_submission_count"] == 2
    result = service.apply(_rows(price_a=130), "MODEL_A")
    by_code = {row["stock_code"]: row for row in result}
    assert by_code["005930"]["price"] == 130


def test_model_lane_worker_install_is_fail_open_and_after_board_platform():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "worker64_guarded_large_bidask.py"
    ).read_text(encoding="utf-8")

    assert "_install_model_lane_fail_open()" in source
    assert "stockboard_model_lane_patch_error.txt" in source
    assert source.rindex("_install_board_platform_fail_open()") < source.rindex(
        "_install_model_lane_fail_open()"
    )
    assert 'controller.top_codes = []' in source
    assert 'controller.pool_codes = []' in source
    assert 'if getattr(controller, "paused", False)' in source
    assert '"model_lane_compute_ms"' in source
    assert '"model_lane_reuse_count"' in source
