from __future__ import annotations

# Install lightweight wrappers before worker modules import the Theme rank and UI
# installers. The wrappers add server-completed ranking/layout behavior without
# adding TR, OpenAPI work, network connections, or collector load.
from realtime_v2.theme_average_view_layout_patch import install_runtime_wrappers
from realtime_v2.theme_average_view_script_hotfix import install_script_hotfix
from realtime_v2.stockboard_global_sort_patch import install as install_stockboard_global_sort
from realtime_v2.stockboard_manual_sort_mode_patch import (
    install as install_stockboard_manual_sort_mode,
)
from realtime_v2.candidate_score_unification_patch import (
    install as install_candidate_score_unification,
)
from realtime_v2.three_lane_display_order_patch import (
    install as install_three_lane_display_order,
)

install_runtime_wrappers()
install_script_hotfix()
install_candidate_score_unification()
install_three_lane_display_order()
install_stockboard_global_sort()
install_stockboard_manual_sort_mode()

__all__: list[str] = []
