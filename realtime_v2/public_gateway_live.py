"""Fresh-port public gateway for the exact current StockBoard v2 UI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2 import public_gateway_core as core

GATEWAY_VERSION = "stockboard_public_live_ui_v1_20260721"
CURRENT_UI_MARKER = "STOCKBOARD_PUBLIC_LIVE_UI_V1_20260721"
PUBLIC_CHROME_CLEANUP_VERSION = "stockboard_public_chrome_cleanup_v2_20260722"
PUBLIC_ROOT_CONTRACT_VERSION = "stockboard_public_root_no_query_v1_20260722"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_UPSTREAM = "http://127.0.0.1:8765"
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

core.ROW_FIELDS = tuple(dict.fromkeys((*core.ROW_FIELDS, *EXTRA_ROW_FIELDS)))
core.STATUS_FIELDS = tuple(dict.fromkeys((*core.STATUS_FIELDS, "universe_count")))

PUBLIC_UI_STYLE = """
<style id="stockboard-public-live-ui-style">
#topbar .board-shell-tab:not(.active),
#topbar .board-shell-new-window,
#row-position-toggle,
#candidate-model-selector,
#counts,
#throughput,
#collector-metrics,
#worker-metrics,
#lag-metrics,
#copy-status,
#latency,
#render-metrics,
#metric-mode-status,
#topbar .small { display:none!important; }

#topbar.topbar {
  height:auto!important;
  min-height:0!important;
  max-height:none!important;
  overflow-y:visible!important;
  scrollbar-gutter:auto!important;
}

#topbar .metric-row:empty { display:none!important; }

.public-readonly-badge {
  color:#7c2d12;
  border-color:#fdba74;
  background:#ffedd5;
  font-weight:800;
}
</style>
""".strip()

PUBLIC_UI_SCRIPT = """
<script id="stockboard-public-live-ui-script">
(function(){
  if(window.location.pathname==='/' && window.location.search){
    window.history.replaceState(null,'','/');
  }

  const PUBLIC_REMOVE_IDS=[
    'copy-status',
    'counts',
    'latency',
    'throughput',
    'collector-metrics',
    'worker-metrics',
    'lag-metrics',
    'render-metrics',
    'metric-mode-status'
  ];

  function ensurePublicReadonlyBadge(){
    const status=document.getElementById('status');
    if(status && !document.querySelector('.public-readonly-badge')){
      const badge=document.createElement('span');
      badge.className='badge public-readonly-badge';
      badge.textContent='공개 읽기 전용 · 현재 UI';
      status.insertAdjacentElement('afterend',badge);
    }
  }

  function cleanPublicTopbar(){
    const topbar=document.getElementById('topbar');
    if(!topbar) return;

    const title=topbar.querySelector('.title');
    if(title) title.textContent='StockBoard v2 Public';

    ensurePublicReadonlyBadge();

    PUBLIC_REMOVE_IDS.forEach(id=>{
      topbar.querySelectorAll(`[id="${id}"]`).forEach(node=>node.remove());
    });

    topbar.querySelectorAll('.small').forEach(node=>node.remove());

    const candidateSelector=topbar.querySelector('#candidate-model-selector');
    if(candidateSelector){
      const label=candidateSelector.closest('label');
      if(label) label.remove();
      else candidateSelector.remove();
    }

    topbar.querySelectorAll('.metric-row').forEach(row=>{
      const visible=Array.from(row.children).some(child=>{
        if(!child.isConnected) return false;
        const style=getComputedStyle(child);
        return !child.hidden && style.display!=='none' && style.visibility!=='hidden';
      });
      if(!visible) row.remove();
    });

    topbar.style.height='auto';
    topbar.style.minHeight='0';
    topbar.style.maxHeight='none';
    topbar.style.overflowY='visible';
    topbar.style.scrollbarGutter='auto';
  }

  let cleanupScheduled=false;
  function schedulePublicTopbarCleanup(){
    if(cleanupScheduled) return;
    cleanupScheduled=true;
    requestAnimationFrame(()=>{
      cleanupScheduled=false;
      cleanPublicTopbar();
    });
  }

  cleanPublicTopbar();
  requestAnimationFrame(cleanPublicTopbar);
  setTimeout(cleanPublicTopbar,100);
  setTimeout(cleanPublicTopbar,500);

  const topbar=document.getElementById('topbar');
  if(topbar && typeof MutationObserver==='function'){
    const observer=new MutationObserver(schedulePublicTopbarCleanup);
    observer.observe(topbar,{childList:true,subtree:true});
    window.__stockboardPublicTopbarObserver=observer;
  }

  window.addEventListener('resize',schedulePublicTopbarCleanup,{passive:true});
  window.addEventListener('orientationchange',schedulePublicTopbarCleanup,{passive:true});
  document.addEventListener('visibilitychange',()=>{
    if(!document.hidden) schedulePublicTopbarCleanup();
  });

  if(typeof window.sendHtsCommand==='function'){
    window.sendHtsCommand=function(code){
      const text=String(code||'').trim();
      if(!/^\\d{6}$/.test(text)) return;
      if(typeof writeClipboardText==='function'){
        writeClipboardText(text).catch(()=>{});
      }
    };
  }
})();
</script>
""".strip()


def _fetch_bytes(
    url: str,
    *,
    accept: str,
    timeout: float = 5.0,
    limit: int = MAX_HTML_BYTES,
) -> tuple[bytes, str]:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "StockBoardPublicLiveGateway/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read(limit + 1)
        status = int(getattr(response, "status", response.getcode()))
        content_type = str(response.headers.get("Content-Type") or "")
    if status != 200:
        raise RuntimeError(f"Upstream returned HTTP {status}: {url}")
    if len(body) > limit:
        raise RuntimeError(f"Upstream response exceeded {limit} bytes: {url}")
    return body, content_type


def fetch_current_public_html(upstream: str) -> bytes:
    upstream = upstream.rstrip("/")
    body, content_type = _fetch_bytes(
        f"{upstream}/?public_live_ui={GATEWAY_VERSION}",
        accept="text/html",
    )
    if "text/html" not in content_type.lower():
        raise RuntimeError(f"Private UI returned {content_type or 'unknown content type'}.")
    html = body.decode("utf-8-sig")
    required = ("StockBoard v2", "/api/v2/stream", "1분대금", "5분강도")
    missing = [marker for marker in required if marker not in html]
    if missing:
        raise RuntimeError("Current private UI markers missing: " + ", ".join(missing))

    html = html.replace(
        "<title>StockBoard v2 Realtime</title>",
        "<title>StockBoard v2 Public</title>",
        1,
    )
    marker = (
        f"<!-- {CURRENT_UI_MARKER} -->\n"
        f'<meta name="stockboard-public-gateway-version" content="{GATEWAY_VERSION}">\n'
        f'<meta name="stockboard-public-chrome-cleanup" content="{PUBLIC_CHROME_CLEANUP_VERSION}">\n'
        f'<meta name="stockboard-public-root-contract" content="{PUBLIC_ROOT_CONTRACT_VERSION}">'
    )
    if "</head>" in html:
        html = html.replace("</head>", f"{PUBLIC_UI_STYLE}\n{marker}\n</head>", 1)
    else:
        html = f"{PUBLIC_UI_STYLE}\n{marker}\n{html}"
    if "</body>" in html:
        html = html.replace("</body>", f"{PUBLIC_UI_SCRIPT}\n</body>", 1)
    else:
        html = f"{html}\n{PUBLIC_UI_SCRIPT}\n"
    return html.encode("utf-8")


class LivePublicDataCache(core.PublicDataCache):
    def health(self) -> dict[str, Any]:
        value = dict(super().health())
        value.update(
            {
                "gateway_version": GATEWAY_VERSION,
                "ui_source": "live_private_worker_html_per_request",
                "ui_contract": CURRENT_UI_MARKER,
                "public_chrome_cleanup": PUBLIC_CHROME_CLEANUP_VERSION,
                "public_root_contract": PUBLIC_ROOT_CONTRACT_VERSION,
                "public_root_path": "/",
                "gateway_port": DEFAULT_PORT,
            }
        )
        return value


class LivePublicGatewayServer(core.PublicGatewayServer):
    def __init__(self, *args, live_upstream: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.live_upstream = live_upstream.rstrip("/")


class LivePublicGatewayHandler(core.PublicGatewayHandler):
    server_version = "StockBoardPublicLive/1.0"

    def _headers(self):
        super()._headers()
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Surrogate-Control", "no-store")
        self.send_header("X-StockBoard-Public-Version", GATEWAY_VERSION)
        self.send_header("X-StockBoard-Public-UI", "live-private-worker-html")
        self.send_header("X-StockBoard-Public-Root", "/")
        self.send_header(
            "X-StockBoard-Public-Chrome-Cleanup",
            PUBLIC_CHROME_CLEANUP_VERSION,
        )
        self.send_header(
            "X-StockBoard-Public-Root-Contract",
            PUBLIC_ROOT_CONTRACT_VERSION,
        )

    def _get(self, head: bool):
        path = urlparse(self.path).path
        if path not in {"/", "/public", "/stockboard_public.html"}:
            return super()._get(head)

        if not self.server.request_slots.acquire(False):
            self._error(503, "server_busy", head)
            return
        try:
            if not self.server.rate_limiter.allow(self._client_id()):
                self._error(429, "rate_limited", head)
                return
            try:
                body = fetch_current_public_html(self.server.live_upstream)
            except Exception:
                self._error(503, "current_ui_unavailable", head)
                return
            self._bytes(body, "text/html; charset=utf-8", head=head)
        finally:
            self.server.request_slots.release()


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard v2 current-UI public gateway")
    parser.add_argument("--host", default=os.getenv("STOCKBOARD_PUBLIC_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=_int_env("STOCKBOARD_PUBLIC_PORT", DEFAULT_PORT, 1, 65535),
    )
    parser.add_argument(
        "--upstream",
        default=os.getenv("STOCKBOARD_PUBLIC_UPSTREAM", DEFAULT_UPSTREAM),
    )
    parser.add_argument(
        "--snapshot-interval-sec",
        type=float,
        default=_float_env(
            "STOCKBOARD_PUBLIC_SNAPSHOT_INTERVAL_SEC",
            1.0,
            0.2,
            5.0,
        ),
    )
    parser.add_argument(
        "--context-interval-sec",
        type=float,
        default=_float_env(
            "STOCKBOARD_PUBLIC_CONTEXT_INTERVAL_SEC",
            15.0,
            5.0,
            120.0,
        ),
    )
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Public gateway must bind to loopback.")
    parsed = urlparse(args.upstream)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit("Public gateway upstream must be a loopback HTTP URL.")

    fetch_current_public_html(args.upstream)

    cache = LivePublicDataCache(
        args.upstream,
        snapshot_interval_sec=args.snapshot_interval_sec,
        context_interval_sec=args.context_interval_sec,
    )
    cache.start()
    server = LivePublicGatewayServer(
        (args.host, args.port),
        LivePublicGatewayHandler,
        cache=cache,
        public_html=b"",
        per_client_rate=_int_env(
            "STOCKBOARD_PUBLIC_REQUESTS_PER_MINUTE",
            240,
            30,
            5000,
        ),
        global_rate=_int_env(
            "STOCKBOARD_PUBLIC_GLOBAL_REQUESTS_PER_MINUTE",
            2400,
            100,
            50000,
        ),
        max_concurrent_requests=_int_env(
            "STOCKBOARD_PUBLIC_MAX_CONCURRENT_REQUESTS",
            64,
            4,
            512,
        ),
        max_stream_clients=_int_env(
            "STOCKBOARD_PUBLIC_MAX_STREAM_CLIENTS",
            20,
            1,
            200,
        ),
        live_upstream=args.upstream,
    )
    print(
        f"StockBoard public live UI gateway http://{args.host}:{args.port}/ "
        f"upstream={args.upstream} version={GATEWAY_VERSION} "
        f"cleanup={PUBLIC_CHROME_CLEANUP_VERSION} "
        f"root={PUBLIC_ROOT_CONTRACT_VERSION}",
        flush=True,
    )
    try:
        server.serve_forever(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        cache.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
