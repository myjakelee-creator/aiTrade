"""Shared Board Platform services for StockBoard, ThemeBoard and future boards."""

from .fast_path_optimize import install as install_fast_path_optimize
from .fast_path_profile import install as install_fast_path_profile
from .hot_path_cprofile import install as install_hot_path_cprofile
from .http_patch import install as install_http_patch
from .model_lane_merge_optimize import install as install_model_lane_merge_optimize


def install(base, large):
    install_http_patch(base, large)
    actual_module = getattr(large, "guarded", large)
    install_fast_path_optimize(actual_module, base)
    install_model_lane_merge_optimize()
    install_fast_path_profile(actual_module, base)
    install_hot_path_cprofile(base)


__all__ = ["install"]
