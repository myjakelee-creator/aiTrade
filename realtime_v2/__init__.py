from __future__ import annotations

# Install lightweight wrappers before worker modules import the Theme rank and UI
# installers. The wrappers add server-completed ranking/layout behavior without
# adding TR, OpenAPI work, network connections, or collector load.
from realtime_v2.previous_trade_value_fail_closed_patch import (
    install as install_previous_trade_value_fail_closed,
)
from realtime_v2.closed_metric_alias_restore_patch import (
    install as install_closed_metric_alias_restore,
)
from realtime_v2.closed_server_metric_completion_patch import (
    install_runtime_wrapper as install_closed_server_metric_completion,
)
from realtime_v2.final_closed_snapshot_projection_runtime_patch import (
    install_runtime_wrapper as install_final_closed_snapshot_projection,
)
from realtime_v2.theme_average_view_layout_patch import install_runtime_wrappers
from realtime_v2.theme_average_view_script_hotfix import install_script_hotfix
from realtime_v2.stockboard_global_sort_patch import install as install_stockboard_global_sort
from realtime_v2.stockboard_manual_sort_mode_patch import (
    install as install_stockboard_manual_sort_mode,
)
from realtime_v2.candidate_json_eight_criteria_patch import (
    install as install_candidate_json_eight_criteria,
)
from realtime_v2.candidate_json_default_policy_patch import (
    install as install_candidate_json_default_policy,
)
from realtime_v2.trade_value_rank_score_patch import (
    install as install_trade_value_rank_score,
)
from realtime_v2.candidate_score_unification_patch import (
    install as install_candidate_score_unification,
)
from realtime_v2.three_lane_display_order_patch import (
    install as install_three_lane_display_order,
)
from realtime_v2.display_order_model_reset_patch import (
    install as install_display_order_model_reset,
)
from realtime_v2.premarket_rollover_priority_patch import (
    install_runtime_wrapper as install_premarket_rollover_priority,
)
from realtime_v2.price_sequence_guard_patch import (
    install_runtime_wrapper as install_price_sequence_guard,
)
from realtime_v2.sse_latest_only_patch import (
    install_runtime_wrapper as install_sse_latest_only,
)
from realtime_v2.price_fast_sse_patch import (
    install_runtime_wrapper as install_price_fast_sse,
)
from realtime_v2.price_fast_sse_recovery_patch import (
    install_runtime_wrapper as install_price_fast_sse_recovery,
)
from realtime_v2.price_fast_flash_patch import (
    install_runtime_wrapper as install_price_fast_flash,
)
from realtime_v2.full_stream_relief_patch import (
    install_runtime_wrapper as install_full_stream_relief,
)
from realtime_v2.metric_delta_stability_patch import (
    install_runtime_wrapper as install_metric_delta_stability,
)
from realtime_v2.metric_fast_sse_patch import (
    install_runtime_wrapper as install_metric_fast_sse,
)
from realtime_v2.metric_fast_one_min_tombstone_patch import (
    install_runtime_wrapper as install_metric_fast_one_min_tombstone,
)
from realtime_v2.closed_metric_rank_hold_patch import (
    install_runtime_wrapper as install_closed_metric_rank_hold,
)
from realtime_v2.ratio_connection_cleanup_patch import (
    install_runtime_wrapper as install_ratio_connection_cleanup,
)
from realtime_v2.after_close_live_hold_patch import (
    install_runtime_wrapper as install_after_close_live_hold,
)
from realtime_v2.after_close_metric_source_guard_patch import (
    install_runtime_wrapper as install_after_close_metric_source_guard,
)
from realtime_v2.after_close_settlement_state_patch import (
    install_runtime_wrapper as install_after_close_settlement_state,
)

install_previous_trade_value_fail_closed()
# Normalize only persisted Worker metric aliases before lifecycle capture/restore.
install_closed_metric_alias_restore()
# Complete only final closed-session row fields after the existing sort pipeline.
install_closed_server_metric_completion()
# Opening-burst replaces State.snapshot; project closed fields after that final cache.
install_final_closed_snapshot_projection()
install_premarket_rollover_priority()
# Price/rate follow Collector callback sequence. Source timestamps are not used for
# price freshness because SOR callbacks can carry interleaved FID20 values.
install_price_sequence_guard()
install_sse_latest_only()
install_price_fast_sse()
install_price_fast_sse_recovery()
install_price_fast_flash()
install_full_stream_relief()
install_metric_delta_stability()
install_metric_fast_sse()
# Metric SSE merges into browser rows; explicit nulls are required to clear stale 0s.
install_metric_fast_one_min_tombstone()
# Metric SSE may refresh active-session rank, but after close the full/checkpoint
# snapshot exclusively owns rank and lane placement.
install_closed_metric_rank_hold()
install_ratio_connection_cleanup()
# Sanitize checkpoint-only metric provenance before the close-hold wrapper is used.
install_after_close_metric_source_guard()
# Keep last-good rows and persist low-frequency checkpoints after close.
install_after_close_live_hold()
# Classify grace/provisional/final/recovery states without changing price flow.
install_after_close_settlement_state()
install_runtime_wrappers()
install_script_hotfix()
install_candidate_json_eight_criteria()
install_candidate_json_default_policy()
install_trade_value_rank_score()
install_candidate_score_unification()
install_three_lane_display_order()
install_display_order_model_reset()
install_stockboard_global_sort()
install_stockboard_manual_sort_mode()

__all__: list[str] = []
