from __future__ import annotations

from typing import Any


def _install_fast_code(module: Any, marker: str) -> bool:
    original = getattr(module, "_code", None)
    if not callable(original) or getattr(module, marker, False):
        return False

    def fast_code(value: Any) -> str:
        # FeatureSnapshot and theme-master codes are already canonical six-digit
        # ASCII strings in the hot path. Return them without rebuilding a digit
        # string. Unusual values retain the original normalization semantics.
        if (
            isinstance(value, str)
            and len(value) == 6
            and value.isascii()
            and value.isdigit()
        ):
            return value
        return original(value)

    module._code = fast_code
    setattr(module, marker, True)
    setattr(module, f"{marker}_original", original)
    return True


def install(theme_module: Any, flow_module: Any | None = None) -> bool:
    """Install a canonical six-digit code fast path without changing semantics.

    Theme summary aggregation revisits overlapping memberships, while flow history
    visits every shared feature row. Both normally receive canonical six-digit
    strings. Avoiding repeated ``str`` conversion, digit filtering and slicing keeps
    the uncommon/suffixed fallback behavior intact and adds no TR, browser or I/O work.
    """

    if not callable(getattr(theme_module, "_code", None)):
        return False

    installed_theme = _install_fast_code(
        theme_module,
        "_stockboard_theme_fast_code_installed",
    )
    installed_flow = False
    if flow_module is not None:
        installed_flow = _install_fast_code(
            flow_module,
            "_stockboard_theme_flow_fast_code_installed",
        )

    theme_module._stockboard_theme_fast_primitives_status = {
        "enabled": True,
        "canonical_format": "six_digit_ascii",
        "fallback_semantics_preserved": True,
        "theme_code_fast_path": bool(
            getattr(theme_module, "_stockboard_theme_fast_code_installed", False)
        ),
        "flow_code_fast_path": bool(
            flow_module is not None
            and getattr(flow_module, "_stockboard_theme_flow_fast_code_installed", False)
        ),
        "additional_tr_allowed": False,
        "browser_calculation_allowed": False,
    }
    return installed_theme or installed_flow
