from __future__ import annotations

"""Inject a permanent UI build identity into StockBoard HTML.

Display only: no QAx, FID, REST, WebSocket, worker state, ranking, timer, or
SSE cadence is changed. The identity file is read once when this module loads.
"""

import json
import re
from html import escape
from pathlib import Path
from typing import Any

MARKER = "STOCKBOARD_V2_BUILD_IDENTITY_20260722"
ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = ROOT / "config" / "stockboard_ui_identity.json"

_FALLBACK_IDENTITY = {
    "schema_version": 1,
    "version": "SBV2-20260722.3",
    "published_at": "2026-07-22 13:24 KST",
    "keyword": "TRADE-VALUE-ROLLOVER",
    "baseline_commit": "6e48d6d",
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


def _version_contains_date(version: str) -> bool:
    return re.search(r"(?<!\d)20\d{6}(?!\d)", str(version or "")) is not None


def _published_date(published_at: str) -> str:
    text = str(published_at or "").strip()
    match = re.search(r"(?<!\d)(20\d{2})[-./]?(\d{2})[-./]?(\d{2})(?!\d)", text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def build_badge_text(identity: dict[str, str] | None = None) -> str:
    values = identity or IDENTITY
    parts = [f"VER {values['version']}"]
    if not _version_contains_date(values["version"]):
        date_text = _published_date(values.get("published_at", ""))
        if date_text:
            parts.append(date_text)
    parts.append(values["keyword"])
    return " · ".join(parts)


def apply_build_identity(html: str) -> str:
    """Return StockBoard HTML with exactly one visible build identity badge."""

    if MARKER in html or 'id="stockboard-build-identity"' in html:
        return html

    topbar_present = 'id="topbar"' in html
    title_start = html.find('<span class="title">')
    if not topbar_present and title_start < 0:
        return html
    if title_start < 0:
        raise RuntimeError("StockBoard build identity title anchor not found")

    title_end = html.find("</span>", title_start)
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
    patched = html[:title_end] + badge + html[title_end:]

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
<meta name="stockboard-ui-version" content="{escape(IDENTITY['version'], quote=True)}">
<!-- {MARKER} -->
"""
    head_end = patched.find("</head>")
    if head_end < 0:
        raise RuntimeError("StockBoard build identity head anchor not found")
    return patched[:head_end] + style + patched[head_end:]
