from __future__ import annotations

# Install lightweight wrappers before worker modules import the Theme rank and UI
# installers. The wrappers add a server-completed average-change view and UI-only
# layout controls without adding TR/OpenAPI work.
from realtime_v2.theme_average_view_layout_patch import install_runtime_wrappers
from realtime_v2.theme_average_view_script_hotfix import install_script_hotfix
from realtime_v2.stockboard_global_sort_patch import install as install_stockboard_global_sort

install_runtime_wrappers()
install_script_hotfix()
install_stockboard_global_sort()

__all__: list[str] = []
