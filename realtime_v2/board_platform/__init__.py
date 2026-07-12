"""Shared Board Platform services for StockBoard, ThemeBoard and future boards."""

from .display_hold_fast import install as install_display_hold_fast
from .fast_path_optimize import install as install_fast_path_optimize
from .fast_path_profile import install as install_fast_path_profile
from .hot_path_cprofile import install as install_hot_path_cprofile
from .http_patch import install as install_http_patch
from .market_session_cache import install as install_market_session_cache
from .model_lane_merge_optimize import install as install_model_lane_merge_optimize
from .previous_daily_fast import install as install_previous_daily_fast
from .session_metric_fast import install as install_session_metric_fast


def install(base, large):
    import realtime_v2.display_hold_policy_patch as display_hold_module
    import realtime_v2.market_session as market_session_module
    import realtime_v2.session_metric_hold_patch as session_metric_module
    from realtime_v2.after_close_recovery import install_worker
    from realtime_v2.after_close_recovery_hardening import install_worker_hardening
    from realtime_v2.after_close_recovery_policy_guard import install as install_policy_guard
    from realtime_v2.after_close_recovery_sampler_guard import (
        install_theme_format_guard,
        install_worker_guard,
    )
    from realtime_v2.after_close_theme_recovery import install as install_theme_recovery

    install_market_session_cache(market_session_module)
    install_display_hold_fast(display_hold_module)
    install_session_metric_fast(session_metric_module)
    install_http_patch(base, large)
    actual_module = getattr(large, "guarded", large)
    install_previous_daily_fast(actual_module)
    install_fast_path_optimize(actual_module, base)
    install_model_lane_merge_optimize()
    install_fast_path_profile(actual_module, base)
    install_hot_path_cprofile(base)
    install_worker(base, large)
    install_worker_hardening(base, large)
    install_worker_guard(base)
    install_policy_guard()
    install_theme_format_guard()
    install_theme_recovery(base)


__all__ = ["install"]
