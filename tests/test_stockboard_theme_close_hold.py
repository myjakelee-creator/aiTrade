from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from realtime_v2.theme_board_patch import ThemeCacheService
from stockboard_theme_engine import load_theme_master


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "event_count": 1,
            "trade_count": 1,
            "market_phase": "closed",
            "market_phase_label": "장마감",
        }
        self.quotes = {
            "000660": {"stock_code":"000660","stock_name":"SK하이닉스","price":270000,"change_rate":5.0,"trade_value_eok":1000,"prev_trade_value_eok":250,"last_valid_execution_strength":180,"last_valid_strength_5m":170,"program_net":100,"large_trade_net_count":10},
            "042700": {"stock_code":"042700","stock_name":"한미반도체","day_close":190000,"seed_change_rate":6.0,"seed_trade_value_eok":500,"prev_trade_value_eok":100,"last_valid_execution_strength":190,"regular_close_strength_1m":180,"program_net":50,"large_trade_net_count":7},
            "058470": {"stock_code":"058470","stock_name":"리노공업","ohlc":{"close":300000},"seed_change_rate":4.0,"seed_trade_value_eok":300,"prev_trade_value_eok":100,"last_valid_execution_strength":160,"one_min_strength":150,"program_net":20,"large_trade_net_count":3},
        }


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_theme_master_has_ten_ranked_themes():
    master = load_theme_master(root() / "config" / "stockboard_theme_master.json")
    ranked = [theme for theme in master.themes.values() if theme.enabled and theme.rank_eligible]
    assert len(ranked) == 10
    assert "AUTO_MOBILITY" in master.themes


def test_close_snapshot_is_restored_until_next_calendar_premarket(tmp_path):
    clock = [datetime(2026, 7, 10, 21, 0)]
    persist_path = tmp_path / "theme_last_close.json"
    service = ThemeCacheService(
        FakeState(),
        root() / "config" / "stockboard_theme_master.json",
        persist_path=persist_path,
        now_provider=lambda: clock[0],
    )
    service.register_client()
    try:
        assert service.refresh(force=True) is True
        assert service.close_hold_active is True
        assert persist_path.is_file()
        payload, _body = service.get_snapshot()
        assert payload["status"]["display_basis"] == "LAST_CLOSE"
        assert payload["status"]["hold_until"].endswith("08:00")
    finally:
        service.unregister_client()

    restored = ThemeCacheService(
        FakeState(),
        root() / "config" / "stockboard_theme_master.json",
        persist_path=persist_path,
        now_provider=lambda: clock[0],
    )
    payload, _body = restored.get_snapshot(force_if_empty=False)
    assert restored.close_hold_active is True
    assert payload["themes"]
    assert payload["status"]["display_basis_text"] == "장마감 마지막값 유지"

    clock[0] = datetime(2026, 7, 13, 8, 0)
    restored.refresh(force=False)
    assert restored.close_hold_active is False
    assert not persist_path.exists()


def test_close_fallback_resolves_last_valid_quote_fields(tmp_path):
    service = ThemeCacheService(
        FakeState(),
        root() / "config" / "stockboard_theme_master.json",
        persist_path=tmp_path / "theme_last_close.json",
        now_provider=lambda: datetime(2026, 7, 10, 21, 0),
    )
    rows, _version, _wait, _probe, _copy, acquired = service.copy_rows(hold_phase=True)
    assert acquired is True
    by_code = {row["stock_code"]: row for row in rows}
    assert by_code["042700"]["price"] == 190000
    assert by_code["042700"]["trade_value_eok"] == 500
    assert by_code["042700"]["execution_strength"] == 190
    assert by_code["042700"]["strength_5m"] == 180
    assert by_code["042700"]["amount_ratio"] == 5
    assert by_code["058470"]["price"] == 300000
    assert by_code["058470"]["strength_5m"] == 150


def test_persisted_payload_is_small_json(tmp_path):
    clock = [datetime(2026, 7, 10, 21, 0)]
    persist_path = tmp_path / "theme_last_close.json"
    service = ThemeCacheService(
        FakeState(),
        root() / "config" / "stockboard_theme_master.json",
        persist_path=persist_path,
        now_provider=lambda: clock[0],
    )
    service.register_client()
    try:
        service.refresh(force=True)
    finally:
        service.unregister_client()
    data = json.loads(persist_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert len(persist_path.read_bytes()) < 100_000
