"""Runtime adapter that exposes the exact current StockBoard UI through the public gateway.

The canonical worker still owns the HTML patch chain. This adapter fetches the already
patched loopback HTML from port 8765, then applies the public read-only injection from
``public_gateway_core``. It also extends the explicit row allowlist for fields used by
the current production columns.
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
            "User-Agent": "StockBoardPublicGateway/1.1",
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
    if "StockBoard v2" not in html or "/api/v2/stream" not in html:
        raise RuntimeError("Private StockBoard UI contract markers were not found.")
    return base.build_public_html(html).encode("utf-8")


base.load_public_html = load_current_public_html
load_public_html = load_current_public_html


if __name__ == "__main__":
    raise SystemExit(base.main())
