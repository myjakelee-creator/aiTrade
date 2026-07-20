from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import realtime_v2
from realtime_v2 import html_portable_rebuild_status_patch as patch

ROOT = Path(__file__).resolve().parents[1]


def _render(monkeypatch) -> str:
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(realtime_v2, "worker64_guarded_large", fake_large, raising=False)
    patch.install()
    source = (ROOT / "docs" / "stockboard_v2.html").read_text(encoding="utf-8-sig")
    return fake_large._ui_safety_patch(source)


def test_empty_board_shows_build_and_retry_progress(monkeypatch):
    rendered = _render(monkeypatch)
    assert patch.MARKER in rendered
    assert "function portableBoardEmptyMessage" in rendered
    assert "직전 거래일 보드 재구성 중" in rendered
    assert "직전 거래일 보드 재구성 재시도 대기" in rendered
    assert "portable_board_completed_count" in rendered
    assert "portable_board_requested_count" in rendered
    assert "portable_board_next_retry_at" in rendered
    assert "portableBoardEmptyMessage(payload,'집중 후보 수신 대기 중입니다.')" in rendered
    assert "portableBoardEmptyMessage(payload,'Top300 Pool 수신 대기 중입니다.')" in rendered


def test_portable_status_html_adds_no_data_or_timer_path():
    source = inspect.getsource(patch)
    assert "/api/" not in source
    assert "new EventSource" not in source
    assert "WebSocket(" not in source
    assert "Thread(" not in source
    assert "setInterval(" not in source
