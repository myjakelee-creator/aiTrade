"""Shared Board Platform services for StockBoard, ThemeBoard and future boards."""

from .display_hold_fast import install as install_display_hold_fast
from .fast_path_optimize import install as install_fast_path_optimize
from .fast_path_profile import install as install_fast_path_profile
from .hot_path_cprofile import install as install_hot_path_cprofile
from .http_patch import install as install_http_patch
from .market_session_cache import install as install_market_session_cache
from .model_lane_merge_optimize import install as install_model_lane_merge_optimize


def install(base, large):
    import realtime_v2.display_hold_policy_patch as display_hold_module
    import realtime_v2.market_session as market_session_module

    install_market_session_cache(market_session_module)
    install_display_hold_fast(display_hold_module)
    install_http_patch(base, large)
    actual_module = getattr(large, "guarded", large)
    install_fast_path_optimize(actual_module, base)
    install_model_lane_merge_optimize()
    install_fast_path_profile(actual_module, base)
    install_hot_path_cprofile(base)


__all__ = ["install"]
