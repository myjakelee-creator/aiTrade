from __future__ import annotations

import re
import traceback
from http import HTTPStatus
from pathlib import Path

MARKER = "STOCKBOARD_V2_HEADER_SORT_WITH_LOCK_20260710"
MANUAL_SORT_KEY = "stockboard.v2.headerSortActive.v1"


def apply_header_sort_patch(html: str) -> str:
    if MARKER in html:
        return html

    html = re.sub(
        r"const key=th\.dataset\.sortKey;\s*sortState=",
        "const key=th.dataset.sortKey; window.__sbv2HeaderSortActive=true; try{localStorage.setItem('stockboard.v2.headerSortActive.v1','1');}catch(_e){} sortState=",
        html,
        count=1,
    )

    old = (
        "const raw=Array.isArray(payload.rows)?payload.rows:[],"
        "displayOrderPaused=!!(payload.display_order&&payload.display_order.paused),"
        "ranked=displayOrderPaused?raw:byCandidateScore(raw),"
        "focusRows=displayOrderPaused?ranked.slice(0,20):sortedRows(ranked.slice(0,20)),"
        "poolRows=displayOrderPaused?ranked.slice(20):sortedRows(ranked.slice(20)),"
        "selectedRows="
    )
    new = (
        "const raw=Array.isArray(payload.rows)?payload.rows:[],"
        "displayOrderPaused=!!(payload.display_order&&payload.display_order.paused),"
        "ranked=displayOrderPaused?raw:byCandidateScore(raw),"
        "__sbv2ClientSort=__sbv2ClientSortActive(displayOrderPaused),"
        "focusBase=ranked.slice(0,20),poolBase=ranked.slice(20),"
        "focusRows=__sbv2ClientSort?sortedRows(focusBase):focusBase,"
        "poolRows=__sbv2ClientSort?sortedRows(poolBase):poolBase,"
        "selectedRows="
    )
    html = html.replace(old, new, 1)

    anchor = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    patch = f'''
  /* {MARKER} */
  window.__sbv2HeaderSortActive = window.__sbv2HeaderSortActive || false;
  function __sbv2ClientSortActive(displayOrderPaused){{
    if(!displayOrderPaused) return true;
    try{{
      return !!window.__sbv2HeaderSortActive || localStorage.getItem('{MANUAL_SORT_KEY}') === '1';
    }}catch(_e){{
      return !!window.__sbv2HeaderSortActive;
    }}
  }}
'''
    if anchor in html:
        html = html.replace(anchor, f"{patch}\n{anchor}", 1)
    return html


def install(base, large_module) -> None:
    handler = base.WebHandler
    if getattr(handler, "_stockboard_header_sort_patch_installed", False):
        return

    original_do_get = handler.do_GET

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path in {"/", "/v2", "/stockboard_v2.html"}:
            try:
                html_path = Path(base.ROOT) / "docs" / "stockboard_v2.html"
                html = html_path.read_text(encoding="utf-8-sig")
                html = large_module._five_min_strength_display_patch(html)
                html = large_module._strip_noisy_tooltips_patch(html)
                html = large_module._ui_safety_patch(html)
                html = apply_header_sort_patch(html)
                body = html.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as error:
                try:
                    runtime = Path(base.RUNTIME_DIR)
                    runtime.mkdir(parents=True, exist_ok=True)
                    (runtime / "html_header_sort_patch_error.txt").write_text(
                        f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        return original_do_get(self)

    handler.do_GET = patched_do_get
    handler._stockboard_header_sort_patch_installed = True
