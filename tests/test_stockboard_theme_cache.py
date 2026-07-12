import json
import threading
from pathlib import Path

from stockboard_theme_cache import ThemeBoardCacheService


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"market_trading_date": "20260713"}
        self.name_by_code = {"005930": "삼성전자"}
        self.quotes = {"005930": {"stock_code": "005930", "trade_value_eok": 100, "change_rate": 1.0}}


def master_file(tmp_path: Path) -> Path:
    path = tmp_path / "themes.json"
    path.write_text(json.dumps({
        "themes": [
            {"theme_id": "SEMICON", "theme_name": "반도체", "members": [{"stock_code": "005930", "role": "primary"}]}
        ]
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_cache_reuses_unchanged_input(tmp_path: Path):
    state = FakeState()
    service = ThemeBoardCacheService(state, master_path=master_file(tmp_path), autostart=False)
    first = service.refresh_now(force=True)
    second = service.refresh_now(force=False)
    assert first["version"] == 1
    assert second["version"] == 1
    assert state.status["theme_cache_hit_count"] >= 1


def test_cache_refreshes_when_input_changes(tmp_path: Path):
    state = FakeState()
    service = ThemeBoardCacheService(state, master_path=master_file(tmp_path), autostart=False)
    service.refresh_now(force=True)
    with state.lock:
        state.quotes["005930"]["trade_value_eok"] = 150
    refreshed = service.refresh_now(force=False)
    assert refreshed["version"] == 2
    assert service.theme_detail("SEMICON")["theme_name"] == "반도체"
