from realtime_v2.board_platform import model_lane
from realtime_v2.board_platform.model_lane_merge_optimize import install


def _engine(rows, model_id=None):
    result = []
    for rank, row in enumerate(reversed(rows), start=1):
        item = dict(row)
        item.update(
            {
                "candidate_model_id": model_id or "MODEL_A",
                "candidate_score": 90 - rank,
                "grade_score": 90 - rank,
                "candidate_status": "READY",
                "model_rank": rank,
                "is_candidate": rank == 1,
                "score_breakdown": {"total": {"score": 90 - rank}},
            }
        )
        result.append(item)
    return result


def test_model_merge_reuses_detached_row_objects_in_place():
    install()
    service = model_lane.StockBoardModelLaneService(_engine)
    rows = [
        {"stock_code": "005930", "price": 100, "rank": 1},
        {"stock_code": "000660", "price": 200, "rank": 2},
    ]
    original_ids = {row["stock_code"]: id(row) for row in rows}

    service.submit(rows, "MODEL_A")
    assert service.compute_once(force=True) is True

    current = [
        {"stock_code": "005930", "price": 111, "rank": 2},
        {"stock_code": "000660", "price": 222, "rank": 1},
    ]
    current_ids = {row["stock_code"]: id(row) for row in current}
    result = service.apply(current, "MODEL_A")

    assert result is current
    assert {row["stock_code"]: id(row) for row in result} == current_ids
    assert {row["stock_code"]: row["price"] for row in result} == {
        "005930": 111,
        "000660": 222,
    }
    assert service.status()["merge_in_place"] is True
    assert original_ids != current_ids


def test_pending_model_fields_are_applied_without_copying_rows():
    install()
    service = model_lane.StockBoardModelLaneService(_engine)
    rows = [{"stock_code": "005930", "candidate_score": 80, "rank": 1}]
    row_id = id(rows[0])

    result = service.apply(rows, "MODEL_B")

    assert result is rows
    assert id(result[0]) == row_id
    assert result[0]["candidate_status"] == "MODEL_PENDING"
    assert result[0]["candidate_score"] is None
