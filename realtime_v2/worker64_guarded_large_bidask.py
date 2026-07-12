from __future__ import annotations

import importlib
import struct
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

large = importlib.import_module("realtime_v2.worker64_guarded_large")
base = large.base

_CROSS_TABLE_NAV_MARKER = "STOCKBOARD_V2_CROSS_TABLE_NAV_20260710"
_CROSS_TABLE_NAV_ANCHOR = (
    "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});"
    "loadCandidateModels();loadContext();markSortHeaders();connectStream();"
)
_CROSS_TABLE_NAV_PATCH = r"""
  /* STOCKBOARD_V2_CROSS_TABLE_NAV_20260710 */
  function __sbv2CrossTableNavRows(){
    const rows = [];
    const seen = new Set();
    [focusBoardEl, poolBoardEl].forEach(table => {
      const body = table && table.tBodies ? table.tBodies[0] : null;
      if(!body) return;
      body.querySelectorAll('tr[data-code]').forEach(row => {
        const code = String(row.dataset.code || '');
        if(!/^\d{6}$/.test(code) || seen.has(code)) return;
        seen.add(code);
        rows.push({row, table, code});
      });
    });
    return rows;
  }

  function __sbv2MoveAcrossBoards(delta){
    const rows = __sbv2CrossTableNavRows();
    if(!rows.length) return false;
    const active = document.activeElement;
    let index = rows.findIndex(item => item.row === active || item.code === selectedCode);
    if(index < 0) index = delta > 0 ? -1 : 0;
    const next = rows[(index + delta + rows.length) % rows.length];
    if(!next || !/^\d{6}$/.test(next.code)) return false;

    if(typeof __sbv2LastNavTable !== 'undefined') __sbv2LastNavTable = next.table;
    if(typeof __sbv2RefocusVisibleRow === 'function'){
      __sbv2RefocusVisibleRow(next.table, next.code);
    }else if(next.row && typeof next.row.focus === 'function'){
      next.row.focus({preventScroll:true});
      if(typeof next.row.scrollIntoView === 'function'){
        next.row.scrollIntoView({block:'nearest', inline:'nearest'});
      }
    }
    selectCodeAndLink(next.code, false, {focus:false});
    return true;
  }

  if(typeof moveSelection === 'function'){
    const __sbv2OriginalCrossTableMoveSelection = moveSelection;
    moveSelection = function(delta){
      if(__sbv2MoveAcrossBoards(delta)) return;
      return __sbv2OriginalCrossTableMoveSelection(delta);
    };
  }
"""


def _runtime_dir() -> Path:
    try:
        return Path(base.RUNTIME_DIR)
    except Exception:
        return ROOT / "data" / "runtime" / "stockboard_v2"


def _write_patch_error(filename: str, error: Exception) -> None:
    try:
        runtime = _runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / filename).write_text(
            f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except Exception:
        pass


def _install_bidask_patch_fail_open() -> None:
    try:
        from realtime_v2.bidask_last_cache_patch import install as install_bidask_last_cache

        install_bidask_last_cache(base)
    except Exception as error:
        _write_patch_error("bidask_worker_patch_error.txt", error)


def _install_display_hold_fail_open() -> None:
    try:
        from realtime_v2.display_hold_policy_patch import install as install_display_hold

        install_display_hold(base)
    except Exception as error:
        _write_patch_error("display_hold_patch_error.txt", error)


def _install_display_hold_ohlc_price_fail_open() -> None:
    try:
        from realtime_v2.display_hold_ohlc_price_patch import install as install_ohlc_price_hold

        install_ohlc_price_hold(base)
    except Exception as error:
        _write_patch_error("display_hold_ohlc_price_patch_error.txt", error)


def _install_session_metric_hold_fail_open() -> None:
    try:
        from realtime_v2.session_metric_hold_patch import install as install_session_metric_hold

        install_session_metric_hold(base)
    except Exception as error:
        _write_patch_error("session_metric_hold_patch_error.txt", error)


def _install_execution_strength_alias_fail_open() -> None:
    try:
        from realtime_v2.execution_strength_alias_patch import install as install_execution_strength_alias

        install_execution_strength_alias(base)
    except Exception as error:
        _write_patch_error("execution_strength_alias_patch_error.txt", error)


def _install_cross_table_navigation_patch_fail_open() -> None:
    try:
        if getattr(large, "_cross_table_navigation_patch_installed", False):
            return

        original_ui_safety_patch = large._ui_safety_patch

        def patched_ui_safety_patch(html: str) -> str:
            patched = original_ui_safety_patch(html)
            if _CROSS_TABLE_NAV_MARKER in patched or _CROSS_TABLE_NAV_ANCHOR not in patched:
                return patched
            return patched.replace(
                _CROSS_TABLE_NAV_ANCHOR,
                f"{_CROSS_TABLE_NAV_PATCH}\n{_CROSS_TABLE_NAV_ANCHOR}",
                1,
            )

        large._ui_safety_patch = patched_ui_safety_patch
        large._cross_table_navigation_patch_installed = True
    except Exception as error:
        _write_patch_error("cross_table_navigation_patch_error.txt", error)


def _install_header_sort_patch_fail_open() -> None:
    try:
        from realtime_v2.html_header_sort_patch import install as install_header_sort

        install_header_sort(base, large)
    except Exception as error:
        _write_patch_error("html_header_sort_patch_error.txt", error)


def _install_theme_board_fail_open() -> None:
    try:
        from realtime_v2.theme_board_patch import install as install_theme_board

        install_theme_board(base)
    except Exception as error:
        _write_patch_error("theme_board_patch_error.txt", error)


def _install_market_supply_hold_fail_open() -> None:
    try:
        from realtime_v2.market_supply_last_valid_patch import install as install_market_supply_hold

        context_module = getattr(large, "guarded", large)
        install_market_supply_hold(context_module, base)
    except Exception as error:
        _write_patch_error("market_supply_last_valid_patch_error.txt", error)


def _install_board_platform_fail_open() -> None:
    try:
        from realtime_v2.board_platform import install as install_board_platform

        install_board_platform(base, large)
    except Exception as error:
        _write_patch_error("board_platform_patch_error.txt", error)


def _install_model_lane_fail_open() -> None:
    try:
        from realtime_v2.board_platform.model_lane import install as install_model_lane
        from realtime_v2.board_platform.stockboard_cache import (
            StockBoardSnapshotCacheService,
        )

        service = install_model_lane(large, base)

        if not getattr(base.WebServer, "_stockboard_model_lane_state_bound", False):
            original_server_init = base.WebServer.__init__

            def patched_server_init(self, address, handler, state):
                original_server_init(self, address, handler, state)
                service.state = state

            base.WebServer.__init__ = patched_server_init
            base.WebServer._stockboard_model_lane_state_bound = True

        if not getattr(large, "_stockboard_model_lane_display_reset_installed", False):
            model_enrich = large.enrich_candidate_model_fields
            reset_state = {"model_id": None}

            def reset_display_order_for_new_model() -> None:
                lane_status = service.status()
                model_id = str(lane_status.get("model_id") or "")
                if not model_id or model_id == reset_state["model_id"]:
                    return
                state = getattr(service, "state", None)
                if state is None:
                    return
                controller = large._display_order_controller(state)
                lock = getattr(controller, "_lock", None)
                if lock is None:
                    return
                with lock:
                    if getattr(controller, "paused", False):
                        return
                    controller.top_codes = []
                    controller.pool_codes = []
                    controller.frozen_codes = []
                    controller.pending_freeze = False
                    challenger = getattr(controller, "_challenger_since", None)
                    if isinstance(challenger, dict):
                        challenger.clear()
                    incumbent = getattr(controller, "_incumbent_out_since", None)
                    if isinstance(incumbent, dict):
                        incumbent.clear()
                    controller.updated_at = large.now_text()
                    controller.version += 1
                reset_state["model_id"] = model_id

            def patched_model_enrich(rows, model_id=None):
                result = model_enrich(rows, model_id=model_id)
                reset_display_order_for_new_model()
                return result

            large.enrich_candidate_model_fields = patched_model_enrich
            large._stockboard_model_lane_display_reset_installed = True

        if not getattr(
            StockBoardSnapshotCacheService,
            "_stockboard_model_lane_status_installed",
            False,
        ):
            original_cache_status = StockBoardSnapshotCacheService.status

            def patched_cache_status(self):
                result = original_cache_status(self)
                lane = getattr(self.state, "stockboard_model_lane", None)
                if lane is None:
                    lane = service
                try:
                    lane_status = lane.status()
                except Exception as error:
                    lane_status = {
                        "state": "ERROR",
                        "last_error": f"{type(error).__name__}: {error}",
                    }
                result.update(
                    {
                        "model_lane_state": lane_status.get("state"),
                        "model_lane_model_id": lane_status.get("model_id"),
                        "model_lane_compute_ms": lane_status.get("compute_ms"),
                        "model_lane_age_ms": lane_status.get("age_ms"),
                        "model_lane_interval_ms": lane_status.get("interval_ms"),
                        "model_lane_compute_count": lane_status.get("compute_count"),
                        "model_lane_reuse_count": lane_status.get("reuse_count"),
                        "model_lane_coalesced": lane_status.get(
                            "coalesced_submission_count"
                        ),
                        "model_lane_pending": lane_status.get("pending"),
                        "model_lane_last_error": lane_status.get("last_error"),
                    }
                )
                return result

            StockBoardSnapshotCacheService.status = patched_cache_status
            StockBoardSnapshotCacheService._stockboard_model_lane_status_installed = True
    except Exception as error:
        _write_patch_error("stockboard_model_lane_patch_error.txt", error)


def _require_64bit_worker() -> None:
    bits = struct.calcsize("P") * 8
    if bits == 64:
        return
    message = (
        f"StockBoard worker requires 64-bit Python, current interpreter is {bits}-bit: "
        f"{sys.executable}"
    )
    try:
        runtime = _runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "worker_python_bits_error.txt").write_text(message, encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(message)


_install_bidask_patch_fail_open()
_install_display_hold_fail_open()
_install_display_hold_ohlc_price_fail_open()
_install_session_metric_hold_fail_open()
_install_execution_strength_alias_fail_open()
_install_cross_table_navigation_patch_fail_open()
_install_header_sort_patch_fail_open()
_install_theme_board_fail_open()
_install_market_supply_hold_fail_open()
_install_board_platform_fail_open()
_install_model_lane_fail_open()

if __name__ == "__main__":
    _require_64bit_worker()
    raise SystemExit(base.main())
