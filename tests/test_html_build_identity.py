from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

from realtime_v2 import html_build_identity_patch as identity_patch

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "stockboard_ui_identity.json"


def test_identity_config_has_required_visible_fields():
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))

    assert payload == {
        "schema_version": 1,
        "version": "SBV2-20260722.2",
        "published_at": "2026-07-22 11:43 KST",
        "keyword": "PUBLIC-OPS-ID",
        "baseline_commit": "f4ce1e1",
    }
    assert "UNKNOWN" not in json.dumps(payload)


def test_apply_build_identity_is_visible_and_idempotent():
    source = (
        '<!doctype html><html><head><title>StockBoard v2 Realtime</title></head>'
        '<body><div id="topbar"><span class="title">StockBoard v2 Realtime</span>'
        '</div></body></html>'
    )
    first = identity_patch.apply_build_identity(source)
    second = identity_patch.apply_build_identity(first)

    assert first == second
    assert first.count('id="stockboard-build-identity"') == 1
    assert identity_patch.build_badge_text() in first
    assert 'data-ui-version="SBV2-20260722.2"' in first
    assert 'data-ui-keyword="PUBLIC-OPS-ID"' in first
    assert "baseline commit: f4ce1e1" in first
    assert identity_patch.MARKER in first


def test_build_identity_contract_is_display_only():
    source = inspect.getsource(identity_patch)
    for forbidden in (
        "requests",
        "urllib",
        "WebSocket(",
        "EventSource(",
        "Thread(",
        "setInterval(",
        "setTimeout(",
        "subprocess",
    ):
        assert forbidden not in source


def test_production_private_and_public_html_show_same_identity():
    script = r'''
import importlib
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask

large = importlib.import_module("realtime_v2.worker64_guarded_large")
identity = importlib.import_module("realtime_v2.html_build_identity_patch")
public = importlib.import_module("realtime_v2.public_gateway")
html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
private_html = large._ui_safety_patch(html)
public_html = public.build_public_html(private_html)
expected = identity.build_badge_text()

assert private_html.count('id="stockboard-build-identity"') == 1
assert public_html.count('id="stockboard-build-identity"') == 1
assert expected in private_html
assert expected in public_html
assert 'data-ui-version="SBV2-20260722.2"' in private_html
assert 'data-ui-keyword="PUBLIC-OPS-ID"' in public_html
assert "UNKNOWN" not in private_html
assert "UNKNOWN" not in public_html
print("private_public_build_identity_ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "private_public_build_identity_ok" in completed.stdout


def test_unrelated_html_fixture_is_untouched():
    source = "<!doctype html><html><head></head><body>fixture</body></html>"
    assert identity_patch.apply_build_identity(source) == source
