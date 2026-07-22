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

    assert payload["schema_version"] == 1
    assert payload["version"] == "SBV2-20260722.1"
    assert payload["published_at"] == "2026-07-22 11:12 KST"
    assert payload["keyword"] == "BASELINE-ID"
    assert payload["baseline_commit"] == "68e3ca3"
    assert "UNKNOWN" not in json.dumps(payload)


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


def test_production_html_always_shows_version_time_and_keyword_once():
    script = r'''
import importlib
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask

large = importlib.import_module("realtime_v2.worker64_guarded_large")
identity = importlib.import_module("realtime_v2.html_build_identity_patch")
html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
patched = large._ui_safety_patch(html)
expected = identity.build_badge_text()

assert identity.MARKER in patched
assert patched.count('id="stockboard-build-identity"') == 1
assert expected in patched
assert 'data-ui-version="SBV2-20260722.1"' in patched
assert 'data-ui-keyword="BASELINE-ID"' in patched
assert "2026-07-22 11:12 KST" in patched
assert "baseline commit: 68e3ca3" in patched
assert "UNKNOWN" not in patched
print("build_identity_production_ok")
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
    assert "build_identity_production_ok" in completed.stdout


def test_unrelated_html_fixture_is_left_untouched():
    script = r'''
from types import SimpleNamespace
import realtime_v2
from realtime_v2 import html_build_identity_patch as patch

fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
realtime_v2.worker64_guarded_large = fake_large
patch.install()
source = "<!doctype html><html><head></head><body>fixture</body></html>"
assert fake_large._ui_safety_patch(source) == source
print("build_identity_fixture_skip_ok")
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
    assert "build_identity_fixture_skip_ok" in completed.stdout
