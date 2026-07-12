"""Shared Board Platform services for StockBoard, ThemeBoard and future boards."""

from .fast_path_optimize import install as install_fast_path_optimize
from .fast_path_profile import install as install_fast_path_profile
from .http_patch import install as install_http_patch


def install(base, large):
    install_http_patch(base, large)
    actual_module = getattr(large, "guarded", large)
    install_fast_path_optimize(actual_module, base)
    install_fast_path_profile(actual_module, base)


__all__ = ["install"]
