"""Runtime adapter that exposes the exact current StockBoard UI through the public gateway.

The canonical worker owns the complete HTML patch chain. This adapter fetches that
already-patched loopback HTML from port 8765, applies only the public read-only
boundary, and publishes an explicit version contract so stale gateway processes can
be detected and replaced before Funnel is enabled.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2 import public_gateway_core as base
from realtime_v2.public_gateway_core import *  # noqa: E402,F401,F403

GATEWAY_VERSION = "stockboard_public_current_ui_v2_20260721"
CURRENT_UI_MARKER = "STOCKBOARD_PUBLIC_CURRENT_UI_V2_20260721"
MAX_HTML_BYTES = 8 * 1024 * 1024

EXTRA_ROW_FIELDS = tuple(
    """
    trade_value_1m_eok
    trade_value_prev_1m_eok
    trade_value_1m_ratio_pct
    trade_value_1m_quality
    strength_5m
    strength_20m
    strength_60m
    strength_status
    large_trade_quality
    large_trade_status
    large_trade_gap_possible
    momentum_badge
    momentum_badge_text
    momentum_state
    prev_close
    prev_close_price
    prev_price
    yesterday_close
    base_price
    reference_price
    candidate_model_id
    candidate_reason
    candidate_grade_reason
    """.split()
)

base.ROW_FIELDS = tuple(dict.fromkeys((*base.ROW_FIELDS, *EXTRA_ROW_FIELDS)))
base.STATUS_FIELDS = tuple(dict.fromkeys((*base.STATUS_FIELDS, "universe_count")))

_PUBLIC_SHELL_STYLE = (
    "#topbar .board-shell-tab:not(.active),"
    "#topbar .board-shell-new-window{display:none!important}"
)
if _PUBLIC_SHELL_STYLE not in base.HTML_INJECTION:
    base.HTML_INJECTION = base.HTML_INJECTION.replace(
        "</style>",
        f"{_PUBLIC_SHELL_STYLE}\n</style>",
        1,
    )

_ORIGINAL_BUILD_PUBLIC_HTML = base.build_public_html
_ORIGINAL_HEALTH = base.PublicDataCache.health
_ORIGINAL_SANITIZE_SNAPSHOT = base.sanitize_snapshot
_ORIGINAL_HEADERS = base.PublicGatewayHandler._headers


def build_current_public_html(private_html: str) -> str:
    """Apply the public boundary to the exact HTML currently served by port 8765."""

    html = _ORIGINAL_BUILD_PUBLIC_HTML(private_html)
    if CURRENT_UI_MARKER not in html:
        marker = (
            f'<!-- {CURRENT_UI_MARKER} -->\n'
            f'<meta name="stockboard-public-gateway-version" content="{GATEWAY_VERSION}">'
        )
        if "</head>" in html:
            html = html.replace("</head>", f"{marker}\n</head>", 1)
        else:
            html = f"{marker}\n{html}"
    html = html.replace("공개 읽기 전용", "공개 읽기 전용 · 현재 UI", 1)
    return html


def current_health(self):
    value = dict(_ORIGINAL_HEALTH(self))
    value.update(
        {
            "gateway_version": GATEWAY_VERSION,
            "ui_source": "live_private_worker_html",
            "ui_contract": CURRENT_UI_MARKER,
        }
    )
    return value


def current_sanitize_snapshot(raw):
    value = _ORIGINAL_SANITIZE_SNAPSHOT(raw)
    value["gateway_version"] = GATEWAY_VERSION
    value["ui_contract"] = CURRENT_UI_MARKER
    status = value.get("status")
    if isinstance(status, dict):
        status["public_gateway_version"] = GATEWAY_VERSION
    return value


def current_headers(self):
    _ORIGINAL_HEADERS(self)
    self.send_header("X-StockBoard-Public-Version", GATEWAY_VERSION)
    self.send_header("X-StockBoard-Public-UI", "current-worker-html")


base.build_public_html = build_current_public_html
base.PublicDataCache.health = current_health
base.sanitize_snapshot = current_sanitize_snapshot
base.PublicGatewayHandler._headers = current_headers

# Re-export the patched callables for tests and direct imports.
build_public_html = build_current_public_html
sanitize_snapshot = current_sanitize_snapshot


def _argument_value(name: str, default: str) -> str:
    prefix = f"{name}="
    for index, value in enumerate(sys.argv[1:]):
        if value == name:
            absolute = index + 2
            if absolute < len(sys.argv):
                return str(sys.argv[absolute])
        if value.startswith(prefix):
            return value[len(prefix) :]
    return default


def _upstream_url() -> str:
    return (
        os.getenv("STOCKBOARD_PUBLIC_UPSTREAM")
        or _argument_value("--upstream", base.DEFAULT_UPSTREAM)
    ).rstrip("/")


def load_current_public_html(_unused_path) -> bytes:
    upstream = _upstream_url()
    parsed = urlparse(upstream)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("Public UI source must be a loopback HTTP URL.")

    request = Request(
        f"{upstream}/",
        headers={
            "Accept": "text/html",
            "User-Agent": "StockBoardPublicGateway/1.2",
        },
    )
    with urlopen(request, timeout=5.0) as response:
        body = response.read(MAX_HTML_BYTES + 1)
        status = int(getattr(response, "status", response.getcode()))
        content_type = str(response.headers.get("Content-Type") or "")

    if status != 200:
        raise RuntimeError(f"Private StockBoard UI returned HTTP {status}.")
    if len(body) > MAX_HTML_BYTES:
        raise RuntimeError("Private StockBoard UI exceeds the public gateway size limit.")
    if "text/html" not in content_type.lower():
        raise RuntimeError(
            f"Private StockBoard UI returned {content_type or 'unknown content type'}."
        )

    html = body.decode("utf-8-sig")
    required = ("StockBoard v2", "/api/v2/stream", "1분대금", "5분강도")
    missing = [marker for marker in required if marker not in html]
    if missing:
        raise RuntimeError(
            "Private StockBoard current UI contract markers were not found: "
            + ", ".join(missing)
        )
    return base.build_public_html(html).encode("utf-8")


base.load_public_html = load_current_public_html
load_public_html = load_current_public_html


if __name__ == "__main__":
    raise SystemExit(base.main())
