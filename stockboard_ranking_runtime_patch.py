"""Runtime bridge from stockboard_engine to StockBoard Ranking Engine.

This bridge keeps the current stockboard_engine API stable while allowing a
new ranking-engine-owned model to run through the existing /api/top100 flow.
"""

from __future__ import annotations

from stockboard_large_trade_accumulator import install_large_trade_accumulator_patch
from stockboard_previous_trade_value import install_previous_trade_value_patch
from stockboard_program_net_cache import install_program_net_cache_patch
from stockboard_ranking_engine import (
    NET_BUY_STRENGTH_V02,
    enrich_net_buy_strength_v02_fields,
)


def install_stockboard_ranking_engine_patch() -> None:
    """Patch stockboard_engine for NET_BUY_STRENGTH_V02 runtime support.

    The standard launcher imports this before stockboard_server imports
    enrich_candidate_fields and fetch_ohlc, so stockboard_server receives the
    patched callables without changing its public interface.
    """
    install_previous_trade_value_patch()
    install_program_net_cache_patch()
    install_large_trade_accumulator_patch()

    import stockboard_engine

    if getattr(stockboard_engine, "_ranking_engine_patch_installed", False):
        return

    original_enrich_candidate_fields = stockboard_engine.enrich_candidate_fields

    def enrich_candidate_fields_with_ranking_engine(rows, model=None):
        candidate_model = model or stockboard_engine.CANDIDATE_SCORE_MODEL
        model_id = str(
            (candidate_model or {}).get("id")
            or (candidate_model or {}).get("version")
            or (candidate_model or {}).get("requested_model_id")
            or ""
        ).strip()
        if model_id == NET_BUY_STRENGTH_V02:
            stockboard_engine.enrich_limit_state_fields(rows)
            return enrich_net_buy_strength_v02_fields(rows, candidate_model)
        return original_enrich_candidate_fields(rows, model)

    stockboard_engine.enrich_candidate_fields = enrich_candidate_fields_with_ranking_engine
    stockboard_engine._ranking_engine_patch_installed = True
