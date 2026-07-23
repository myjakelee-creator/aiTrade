from __future__ import annotations

from pathlib import Path


def _runtime_init_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "realtime_v2" / "__init__.py"
    ).read_text(encoding="utf-8")


def test_runtime_init_does_not_install_price_time_monotonic_wrapper():
    source = _runtime_init_source()

    assert "install_price_time_monotonic" not in source
    assert "from realtime_v2.price_time_monotonic_patch import" not in source
    assert "Do not reinstall price_time_monotonic_v1" in source


def test_arrival_price_restore_keeps_existing_trade_and_sse_install_order():
    source = _runtime_init_source()

    assert source.index("install_premarket_rollover_priority()") < source.index(
        "install_sse_latest_only()"
    )
    assert source.index("install_sse_latest_only()") < source.index(
        "install_price_fast_sse()"
    )


def test_deprecated_monotonic_module_remains_unreferenced_by_production_init():
    source = _runtime_init_source()

    # The old module may remain in the repository for diagnosis/history, but production
    # package initialization must not import or execute it.
    assert "price_time_monotonic_patch" not in source
