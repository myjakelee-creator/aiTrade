from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.theme_projection_fast_primitives_patch import install


def test_canonical_six_digit_code_uses_fast_path_and_unusual_values_fall_back():
    theme_calls: list[object] = []
    flow_calls: list[object] = []

    def theme_fallback(value):
        theme_calls.append(value)
        return "THEME_FALLBACK"

    def flow_fallback(value):
        flow_calls.append(value)
        return "FLOW_FALLBACK"

    theme_module = SimpleNamespace(_code=theme_fallback)
    flow_module = SimpleNamespace(_code=flow_fallback)

    assert install(theme_module, flow_module) is True

    assert theme_module._code("005930") == "005930"
    assert flow_module._code("000660") == "000660"
    assert theme_calls == []
    assert flow_calls == []

    assert theme_module._code("A005930") == "THEME_FALLBACK"
    assert flow_module._code("005930_AL") == "FLOW_FALLBACK"
    assert theme_calls == ["A005930"]
    assert flow_calls == ["005930_AL"]


def test_fast_primitive_install_is_idempotent_and_fail_open_for_minimal_modules():
    fallback = lambda value: str(value)
    theme_module = SimpleNamespace(_code=fallback)
    flow_module = SimpleNamespace(_code=fallback)

    assert install(theme_module, flow_module) is True
    wrapped_theme = theme_module._code
    wrapped_flow = flow_module._code

    assert install(theme_module, flow_module) is False
    assert theme_module._code is wrapped_theme
    assert flow_module._code is wrapped_flow

    minimal = SimpleNamespace()
    assert install(minimal, flow_module) is False


def test_fast_primitive_policy_preserves_server_only_boundaries():
    theme_module = SimpleNamespace(_code=lambda value: str(value))
    flow_module = SimpleNamespace(_code=lambda value: str(value))
    install(theme_module, flow_module)

    status = theme_module._stockboard_theme_fast_primitives_status
    assert status["enabled"] is True
    assert status["fallback_semantics_preserved"] is True
    assert status["additional_tr_allowed"] is False
    assert status["browser_calculation_allowed"] is False
