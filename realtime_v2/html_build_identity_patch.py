from __future__ import annotations

"""Display an immutable UI build identity in the shared StockBoard top bar.

This is a display-only patch. It does not change QAx, FID, REST, WebSocket,
worker state, ranking, market-session logic, timers, or SSE cadence.
"""

import json
from html import escape
from pathlib import Path
from typing import Any

MARKER = "STOCKBOARD_V2_BUILD_IDENTITY_20260722"
ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = ROOT / "config" / "stockboard_ui_identity.json"

_FALLBACK_IDENTITY = {
    "schema_version": 1,
    "version": "SBV2-20260722.1",
    "published_at": "2026-07-22 11:12 KST",
    "keyword": "BASELINE-ID",
    "baseline_commit": "68e3ca3",
}


def _clean(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def load_identity() -> dict[str, str]:
    payload: dict[str, Any] = {}
    try:
        raw = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            payload = raw
    except (OSError, ValueError, TypeError):
        payload = {}

    return {
        "version": _clean(payload.get("version"), _FALLBACK_IDENTITY["version"]),
        "published_at": _clean(
            payload.get("published_at"), _FALLBACK_IDENTITY["published_at"]
        ),
        "keyword": _clean(payload.get("keyword"), _FALLBACK_IDENTITY["keyword"]),
        "baseline_commit": _clean(
            payload.get("baseline_commit"), _FALLBACK_IDENTITY["baseline_commit"]
        ),
    }


IDENTITY = load_identity()


def build_badge_text(identity: dict[str, str] | None = None) -> str:
    values = identity or IDENTITY
    return (
        f"VER {values['version']} · {values['published_at']} · {values['keyword']}"
    )


def install() -> None:
    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_build_identity_html_installed", False):
        return

    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        topbar_present = 'id="topbar"' in patched
        title_start = patched.find('<span class="title">')
        if not topbar_present and title_start < 0:
            # Unrelated isolated HTML test fixtures are intentionally left untouched.
            return patched
        if title_start < 0:
            raise RuntimeError("StockBoard build identity title anchor not found")

        title_end = patched.find("</span>", title_start)
        if title_end < 0:
            raise RuntimeError("StockBoard build identity title closing anchor not found")
        title_end += len("</span>")

        text = build_badge_text()
        tooltip = (
            f"UI version: {IDENTITY['version']}\n"
            f"published: {IDENTITY['published_at']}\n"
            f"keyword: {IDENTITY['keyword']}\n"
            f"baseline commit: {IDENTITY['baseline_commit']}"
        )
        badge = (
            f'<span id="stockboard-build-identity" class="badge" '
            f'data-ui-version="{escape(IDENTITY["version"], quote=True)}" '
            f'data-ui-keyword="{escape(IDENTITY["keyword"], quote=True)}" '
            f'title="{escape(tooltip, quote=True)}">{escape(text)}</span>'
        )
        patched = patched[:title_end] + badge + patched[title_end:]

        style = f"""
<style id="stockboard-v2-build-identity-style">
  #stockboard-build-identity {{
    color:#1e3a8a;
    border-color:#93c5fd;
    background:#eff6ff;
    font-weight:800;
    font-variant-numeric:tabular-nums;
  }}
</style>
<!-- {MARKER} -->
"""
        head_end = patched.find("</head>")
        if head_end < 0:
            raise RuntimeError("StockBoard build identity head anchor not found")
        return patched[:head_end] + style + patched[head_end:]

    large._ui_safety_patch = patched_ui_safety_patch
    large._build_identity_html_installed = True
    large._build_identity_version = IDENTITY["version"]
    large._build_identity_keyword = IDENTITY["keyword"]
