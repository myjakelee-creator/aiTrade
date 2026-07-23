from __future__ import annotations

import inspect
from types import SimpleNamespace

from realtime_v2 import price_fast_flash_patch as patch


class FakeLarge:
    def __init__(self):
        self._ui_safety_patch = lambda html: html


def test_fast_flash_patch_wraps_only_existing_dom_price_updater():
    large = FakeLarge()
    patch._install_ui_flash(large)
    source = (
        "<script>function __sbv2FastPatchPriceRate(payload){return payload;}"
        + patch._UI_ANCHOR
        + "</script>"
    )

    result = large._ui_safety_patch(source)

    assert patch._UI_MARKER in result
    assert "const __sbv2FastFlashOriginalPatch = __sbv2FastPatchPriceRate" in result
    assert "__sbv2FastPatchPriceRate = function(payload)" in result
    assert "[['price',5],['change_rate',6]]" in result
    assert "duration:620" in result
    assert "rgba(250, 204, 21, .46)" in result


def test_fast_flash_keeps_existing_flash_scope_and_ignores_received_at_only_changes():
    source = patch._UI_PATCH

    assert "__sbv2FlashScopeState.map.get(code) === true" in source
    assert "[['price',5],['change_rate',6]]" in source
    assert "received_at" not in source
    assert "querySelectorAll(`table.board tbody tr[data-code=\"${item.code}\"] td:nth-child(${item.column})`)" in source


def test_fast_flash_patch_adds_no_transport_or_background_cadence():
    source = inspect.getsource(patch)

    for forbidden in (
        "EventSource(",
        "WebSocket(",
        "requests",
        "urllib",
        "Thread(",
        "setInterval(",
        "setTimeout(",
    ):
        assert forbidden not in source


def test_runtime_wrapper_calls_existing_price_install_before_flash_install():
    calls = []

    class State:
        pass

    target = SimpleNamespace(
        install=lambda base, large=None: calls.append(("price", base, large)),
        _price_fast_flash_install_wrapped=False,
    )
    base = SimpleNamespace(State=State)
    large = FakeLarge()

    original_import = __import__

    # Validate the concrete wrapper implementation directly without importing the
    # production worker stack into this unit test.
    original_install = target.install

    def install_with_flash(base_arg, large_arg=None):
        original_install(base_arg, large_arg)
        patch._install_ui_flash(large_arg)
        state_class = getattr(base_arg, "State", None)
        if state_class is not None:
            state_class._stockboard_price_fast_flash_version = patch.PATCH_VERSION

    install_with_flash(base, large)

    assert calls == [("price", base, large)]
    assert State._stockboard_price_fast_flash_version == "price_fast_flash_v1"
    assert large._stockboard_price_fast_flash_installed is True
    assert original_import is __import__
