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
        "version": "SBV2-20260722.3",
        "published_at": "2026-07-22 13:24 KST",
        "keyword": "TRADE-VALUE-ROLLOVER",
        "baseline_commit": "6e48d6d",
    }
    assert "UNKNOWN" not in json.dumps(payload)


def test_dated_version_omits_duplicate_visible_date_and_time():
    assert identity_patch.build_badge_text() == (
        "VER SBV2-20260722.3 · TRADE-VALUE-ROLLOVER"
    )


def test_undated_version_adds_date_only_not_time():
    text = identity_patch.build_badge_text(
        {
            "version": "SBV2-R4",
            "published_at": "2026-07-22 13:24 KST",
            "keyword": "UNDATED-CHECK",
            "baseline_commit": "abc1234",
        }
    )
    assert text == "VER SBV2-R4 · 2026-07-22 · UNDATED-CHECK"
    assert "13:24" not in text


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
    assert (
        ">VER SBV2-20260722.3 · TRADE-VALUE-ROLLOVER</span>" in first
    )
    assert 'data-ui-version="SBV2-20260722.3"' in first
    assert 'data-ui-keyword="TRADE-VALUE-ROLLOVER"' in first
    assert "published: 2026-07-22 13:24 KST" in first
    assert "baseline commit: 6e48d6d" in first
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
expected = "VER SBV2-20260722.3 · TRADE-VALUE-ROLLOVER"

assert private_html.count('id="stockboard-build-identity"') == 1
assert public_html.count('id="stockboard-build-identity"') == 1
assert f">{expected}</span>" in private_html
assert f">{expected}</span>" in public_html
assert 'data-ui-version="SBV2-20260722.3"' in private_html
assert 'data-ui-keyword="TRADE-VALUE-ROLLOVER"' in public_html
assert "published: 2026-07-22 13:24 KST" in private_html
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
