from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

from realtime_v2 import theme_projection_engine as theme_projection_module
from realtime_v2.theme_average_view_layout_patch import install_runtime_wrappers

# Install the average-view/UI extension before this module captures any rank/UI install
# aliases. This explicit ordering is required on the production worker import path;
# relying only on package __init__ ordering proved insufficient on PC1.
install_runtime_wrappers()

from realtime_v2 import worker_theme_dual_rank_ui_patch as theme_dual_rank_ui_module
from realtime_v2.theme_leader_detail_display_patch import (
    install as install_theme_leader_detail_display,
)
from realtime_v2.theme_leader_selection_patch import (
    install as install_theme_leader_selection,
)
from realtime_v2.theme_projection_dual_rank_patch import (
    install as install_theme_dual_rank,
)
from realtime_v2.theme_projection_performance_accounting_patch import (
    install as install_theme_performance_accounting,
)
from realtime_v2.theme_selected_detail_runtime import ThemeSelectedDetailRuntime
from realtime_v2.worker_opening_load_diagnostics_patch import (
    install as install_opening_load_diagnostics,
)
from realtime_v2.worker_theme_dual_rank_ui_patch import (
    install as install_theme_dual_rank_ui,
)


# Dual rank must complete first so the leader wrapper can precisely score only the
# union of the top momentum and money views. The average-view wrapper is installed
# around the dual-rank install above and adds average_rows without changing leader
# precision scope. Selected detail remains always precise. Performance accounting is
# installed last and sees the final named phase timings.
install_theme_dual_rank(theme_projection_module)
install_theme_leader_selection(theme_projection_module)
install_theme_leader_detail_display(theme_projection_module)
install_theme_performance_accounting(theme_projection_module)


ROOT = Path(__file__).resolve().parents[1]
_CLIENT_PATCH = r"""
<script>
/* THEMEBOARD_SELECTED_DETAIL_LATEST_ONLY_20260713 */
let __themeDetailRetryTimer=0;
let __themeDetailInputFeatureVersion=0;
loadDetail=async function(force){
  if(!selectedThemeId||!lastPayload)return;
  const summaryFeatureVersion=Number(lastPayload.input_feature_version||0);
  if(!force&&__themeDetailInputFeatureVersion>=summaryFeatureVersion)return;
  try{
    const response=await fetch(`/api/v2/hub/theme/detail?theme_id=${encodeURIComponent(selectedThemeId)}&ts=${Date.now()}`,{cache:'no-store'});
    if(response.status===202){
      const waiting=await response.json();
      detailBody.innerHTML=`<tr><td colspan="15" class="empty">선택 테마 상세 준비 중 · ${esc(waiting.theme_id||selectedThemeId)}</td></tr>`;
      clearTimeout(__themeDetailRetryTimer);
      __themeDetailRetryTimer=setTimeout(()=>loadDetail(true),250);
      return;
    }
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const payload=await response.json();
    const theme=payload.theme||{};
    const members=Array.isArray(theme.members)?theme.members:[];
    __themeDetailInputFeatureVersion=Number(payload.input_feature_version||summaryFeatureVersion);
    lastDetailVersion=Number(payload.projection_version||0);
    byId('detailTitle').textContent=`${theme.theme_name||'선택 테마'} · ${theme.active_member_count??0}/${theme.master_member_count??0}종목 · Coverage ${theme.coverage_text||'-'} · 상세 ${payload.calculate_ms??'-'}ms`;
    detailBody.innerHTML=members.length?members.map(memberRowHtml).join(''):'<tr><td colspan="15" class="empty">유효 구성종목 없음</td></tr>';
    bindStockLinks(detailBody);
  }catch(error){
    detailBody.innerHTML=`<tr><td colspan="15" class="empty">상세 조회 실패: ${esc(error.message)}</td></tr>`;
  }
};
</script>
"""


def install(base) -> None:
    """Attach one selected-theme detail worker after the shared Hub patch."""

    state_class = base.State
    if getattr(state_class, "_stockboard_theme_selected_detail_installed", False):
        return

    original_init = state_class.__init__
    original_snapshot = state_class.snapshot
    original_do_get = base.WebHandler.do_GET

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        summary_runtime = getattr(self, "theme_projection_runtime", None)
        summary_worker = getattr(summary_runtime, "worker", None)
        summary_builder = getattr(summary_worker, "builder", None)
        hub = getattr(self, "board_data_hub", None)
        if hub is None or summary_builder is None:
            self.theme_selected_detail_runtime = None
            with self.lock:
                self.status["theme_selected_detail_enabled"] = False
                self.status["theme_selected_detail_last_error"] = (
                    "summary runtime or board data hub unavailable"
                )
            return
        runtime = ThemeSelectedDetailRuntime(
            hub,
            summary_builder,
            min_interval_ms=1000,
        )
        runtime.start()
        self.theme_selected_detail_runtime = runtime
        extension_marker = "THEMEBOARD_AVERAGE_VIEW_RESIZABLE_COLUMNS_20260714"
        extension_loaded = (
            extension_marker in getattr(theme_dual_rank_ui_module, "_STYLE", "")
            and extension_marker in getattr(theme_dual_rank_ui_module, "_SCRIPT", "")
        )
        with self.lock:
            self.status["theme_selected_detail_enabled"] = True
            self.status["theme_selected_detail_queue"] = "latest_only_depth_1"
            self.status["theme_selected_detail_http_calculation_allowed"] = False
            self.status["theme_all_member_detail_generation_allowed"] = False
            self.status["theme_dual_server_rankings_enabled"] = True
            self.status["theme_default_view"] = "momentum"
            self.status["theme_browser_sort_allowed"] = False
            self.status["theme_average_view_layout_extension_loaded"] = extension_loaded
            self.status["theme_average_view_layout_marker"] = (
                extension_marker if extension_loaded else None
            )
            self.status["theme_leader_precision_scope"] = (
                "top_momentum_money_union_plus_selected_detail"
            )

    def patched_snapshot(self, limit: int = 300) -> dict[str, Any]:
        payload = original_snapshot(self, limit)
        runtime = getattr(self, "theme_selected_detail_runtime", None)
        hub = getattr(self, "board_data_hub", None)
        if runtime is not None and hub is not None:
            feature_version = hub.borrow_feature_snapshot()[0]
            runtime.submit(feature_version)
            status = payload.get("status") if isinstance(payload, dict) else None
            if isinstance(status, dict):
                status["theme_selected_detail"] = runtime.status()
        return payload

    def detail_projection(hub):
        return hub.projection_snapshot("theme_detail") if hub is not None else None

    def send_theme_html(handler) -> None:
        path = ROOT / "docs" / "themeboard.html"
        if not path.is_file():
            handler._json(
                {"error": "ThemeBoard HTML unavailable"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        html = path.read_text(encoding="utf-8")
        if "THEMEBOARD_SELECTED_DETAIL_LATEST_ONLY_20260713" not in html:
            html = html.replace("</body>", f"{_CLIENT_PATCH}\n</body>", 1)
        body = html.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path in {"/theme", "/themeboard", "/themeboard.html"}:
            send_theme_html(self)
            return

        if parsed.path == "/api/v2/hub/theme/detail/status":
            runtime = getattr(
                self.server.state, "theme_selected_detail_runtime", None
            )
            self._json(
                runtime.status()
                if runtime is not None
                else {
                    "enabled": False,
                    "status": "UNAVAILABLE",
                }
            )
            return

        if parsed.path != "/api/v2/hub/theme/detail":
            return original_do_get(self)

        query = base.parse_qs(parsed.query)
        selected_id = str((query.get("theme_id") or [""])[0] or "").strip()
        runtime = getattr(self.server.state, "theme_selected_detail_runtime", None)
        hub = getattr(self.server.state, "board_data_hub", None)
        if not selected_id:
            self._json(
                {"error": "theme_id is required"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if runtime is None or hub is None:
            self._json(
                {"error": "theme detail runtime unavailable"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return

        runtime.select(selected_id)
        projection = detail_projection(hub)
        raw_payload = (
            projection.get("payload")
            if isinstance(projection, dict)
            and isinstance(projection.get("payload"), dict)
            else None
        )
        ready = (
            isinstance(raw_payload, dict)
            and raw_payload.get("status") == "READY"
            and str(raw_payload.get("theme_id") or "") == selected_id
            and isinstance(raw_payload.get("theme"), dict)
        )
        if not ready:
            self._json(
                {
                    "schema_version": 1,
                    "source": "board_data_hub_theme_detail",
                    "status": "BUILDING",
                    "theme_id": selected_id,
                    "projection_version": (
                        projection.get("projection_version")
                        if isinstance(projection, dict)
                        else None
                    ),
                    "input_feature_version": (
                        projection.get("input_feature_version")
                        if isinstance(projection, dict)
                        else None
                    ),
                    "runtime": runtime.status(),
                    "policy": {
                        "http_calculation_allowed": False,
                        "request_action": "latest_only_selection_enqueue",
                    },
                },
                status=HTTPStatus.ACCEPTED,
            )
            return

        self._json(
            {
                "schema_version": 3,
                "source": "board_data_hub_theme_detail",
                "status": "READY",
                "theme_id": selected_id,
                "projection_version": projection.get("projection_version"),
                "input_feature_version": projection.get(
                    "input_feature_version"
                ),
                "published_at": projection.get("published_at"),
                "calculate_ms": raw_payload.get("calculate_ms"),
                "theme": raw_payload.get("theme"),
                "policy": raw_payload.get("policy"),
                "leader_detail_status": raw_payload.get("leader_detail_status"),
            }
        )

    state_class.__init__ = patched_init
    state_class.snapshot = patched_snapshot
    base.WebHandler.do_GET = patched_do_get
    state_class._stockboard_theme_selected_detail_installed = True

    # Install last so the Theme page uses the display-only server ranking switch
    # while the detail API above remains the underlying cache source. The imported
    # install alias is guaranteed to include the average-view/column-layout wrapper
    # because install_runtime_wrappers() ran before the alias was captured.
    install_theme_dual_rank_ui(base)
    install_opening_load_diagnostics(base)
