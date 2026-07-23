from __future__ import annotations


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_final_closed_snapshot_projection_wrapped", False):
        return
    original_install = opening_module.install

    def install(base) -> None:
        original_install(base)
        from realtime_v2.final_closed_snapshot_projection_patch import install as install_final

        install_final(base)

    opening_module.install = install
    opening_module._final_closed_snapshot_projection_wrapped = True
