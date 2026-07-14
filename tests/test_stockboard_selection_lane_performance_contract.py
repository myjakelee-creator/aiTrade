from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "realtime_v2" / "candidate_score_unification_patch.py"
LANES = ROOT / "realtime_v2" / "three_lane_display_order_patch.py"
MODEL_RESET = ROOT / "realtime_v2" / "display_order_model_reset_patch.py"
MANUAL_SORT = ROOT / "realtime_v2" / "stockboard_manual_sort_mode_patch.py"
WORKER_UI = ROOT / "realtime_v2" / "worker64_guarded_large_bidask.py"
INIT = ROOT / "realtime_v2" / "__init__.py"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def selection_layers():
    return (CANDIDATE, LANES, MODEL_RESET, MANUAL_SORT)


def test_new_selection_layers_are_valid_python():
    for path in selection_layers():
        ast.parse(text(path))


def test_new_selection_layers_add_no_collector_openapi_tr_or_transport_work():
    combined = "\n".join(text(path) for path in selection_layers())
    for forbidden in (
        "dynamicCall",
        "QAxWidget",
        "SetRealReg",
        "CommConnect",
        "GetCommRealData",
        "requests.",
        "socket.",
        "Thread(",
        "EventSource(",
        "fetch('/api",
        'fetch("/api',
    ):
        assert forbidden not in combined


def test_candidate_order_uses_one_primary_sort_instead_of_three_funnel_sorts():
    source = text(CANDIDATE)
    assert source.count("ordered = sorted(") == 1
    assert "top50_sorted = sorted" not in source
    assert "top20_sorted = sorted" not in source
    assert "source = sorted" not in source


def test_lane_controller_moves_at_most_one_boundary_pair_per_cycle():
    source = text(LANES)
    assert "hot_changed = self._maybe_hot_swap_locked" in source
    assert "if not hot_changed:" in source
    assert "self._maybe_warm_swap_locked" in source
    assert "Move at most one boundary pair" in source


def test_model_reset_is_installed_after_three_lane_controller():
    source = text(INIT)
    assert source.index("install_three_lane_display_order()") < source.index(
        "install_display_order_model_reset()"
    )


def test_existing_100_row_fast_price_path_remains_the_update_guarantee():
    source = text(WORKER_UI)
    assert '"/api/v2/snapshot?limit=100&ts=${Date.now()}"' in source
    assert '"/api/v2/stream?limit=100&interval_ms=100&ts=${Date.now()}"' in source
    assert "document.querySelectorAll('table.board tbody tr[data-code]')" in source
    assert "function __sbv2FastPatchPriceRate(payload)" in source


def test_no_new_selection_rank_column_is_added():
    combined = "\n".join(text(path) for path in (LANES, MANUAL_SORT, WORKER_UI))
    assert "label:'선발순위'" not in combined
    assert 'label:"선발순위"' not in combined
