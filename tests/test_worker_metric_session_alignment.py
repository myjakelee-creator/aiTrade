from __future__ import annotations

import ast
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import realtime_v2.worker_metric_provenance_patch as provenance
from realtime_v2.worker_realtime_strength_ws_top20_patch import resolve_top_codes
from realtime_v2.worker_rest_live_metrics_aftermarket_patch import install as install_aftermarket
from realtime_v2.worker_rest_live_metrics_patch import CONFIG_PATH, RestLiveMetricUpdater
from realtime_v2.worker_rest_metrics_before_market_patch import install as install_before_market

ROOT = Path(__file__).resolve().parents[1]
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"
TOP20_PATCH = ROOT / "realtime_v2" / "worker_realtime_strength_ws_top20_patch.py"
PROVENANCE_PATCH = ROOT / "realtime_v2" / "worker_metric_provenance_patch.py"
STRENGTH5_PATCH = ROOT / "realtime_v2" / "worker_strength5_only_patch.py"
MARKET_MANAGER_PATCH = ROOT / "realtime_v2" / "worker_market_metric_session_manager.py"
AHK_PATH = ROOT / "scripts" / "stockboard_kiwoom_link_v1.ahk"
RENDER_PATCH = ROOT / "realtime_v2" / "html_opening_render_guard_patch.py"


class MinimalState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {}
        self.daily_values_by_code = {}
        self.daily_dirty = False

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def apply_program_net_values(self, values, source, status):
        for code, value in values.items():
            numeric = value.get("program_net") if isinstance(value, dict) else value
            self.daily_values_by_code.setdefault(code, {}).update(
                {
                    "program_net": numeric,
                    "program_net_source": source,
                    "program_net_status": status,
                }
            )
        return len(values)

    def apply_rest_live_metric_values(self, code, values, metric):
        self.daily_values_by_code.setdefault(code, {}).update(values)
        return 1

    def apply_realtime_strength_ws(self, event):
        code = event["stock_code"]
        self.daily_values_by_code.setdefault(code, {}).update(event)
        return True


class MinimalBase:
    State = MinimalState
    DAILY_PERSIST_KEYS = ()


def _config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_before_market_backfill_is_integrated_and_large_trade_disabled():
    install_aftermarket()
    install_before_market()
    updater = RestLiveMetricUpdater(MinimalState(), config=_config())
    updater._minute_now = lambda: 90

    assert updater._session_phase() == "before_market"
    assert updater._in_regular_session() is True
    assert updater._query_code("000660") == "000660_AL"

    policy = _config()["session_manager"]["phase_policies"]["before_market"]
    assert policy["scope"] == 100
    assert policy["missing_only"] is True
    assert policy["intervals"]["bidask"]["s1"] == 900
    assert policy["intervals"]["strength"]["s1"] == 900
    assert policy["intervals"]["large_trade"] == {
        "s1": 0,
        "top20": 0,
        "top100": 0,
    }


def test_program_poll_time_is_separate_from_previous_session_source_date(monkeypatch):
    monkeypatch.setattr(
        provenance,
        "market_session_now",
        lambda now=None: SimpleNamespace(
            phase="before_market",
            trading_date="20260716",
            calendar_date="20260716",
        ),
    )
    monkeypatch.setattr(
        provenance,
        "last_completed_trading_date",
        lambda now=None: "20260715",
    )

    class LocalState(MinimalState):
        pass

    class LocalBase:
        State = LocalState
        DAILY_PERSIST_KEYS = ()

    provenance.install(LocalBase)
    state = LocalState()
    state.apply_program_net_values(
        {"000660": {"program_net": 5290}},
        "ka90004_tr_singleflight",
        "ok",
    )

    value = state.daily_values_by_code["000660"]
    assert value["program_net"] == 5290
    assert value["program_source_trading_date"] == "20260715"
    assert value["_session_hold_program_date"] == "20260715"


def test_top20_resolver_uses_rank_and_caps_symbols():
    state = MinimalState()
    state.quotes = {
        f"{index:06d}": {
            "stock_code": f"{index:06d}",
            "rank": index,
            "trade_value_eok": 1000 - index,
        }
        for index in range(1, 26)
    }

    codes = resolve_top_codes(state, 20)

    assert len(codes) == 20
    assert codes[0] == "000001"
    assert codes[-1] == "000020"


def test_ahk_selects_unique_ranked_edit6_instead_of_rejecting_all_duplicates():
    source = AHK_PATH.read_text(encoding="utf-8-sig")

    assert "ScoreTargetControl" in source
    assert "selected unique highest score" in source
    assert "ahk_exe nkre.exe" in source
    assert "if (matches.Length() > 1)" not in source
    assert "PostMessage, 0x100" in source


def test_opening_render_guard_follows_calendar_and_keeps_price_collector_unchanged():
    source = RENDER_PATCH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "regular_start" in source
    assert "start+10?1000:500" in source
    assert "__sbv2HeavyRenderIntervalMs()" in source

    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "subprocess",
        "Start-Process",
    ):
        assert forbidden not in source

    collector = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector


def test_ka10046_patch_keeps_only_five_minute_fields():
    source = STRENGTH5_PATCH.read_text(encoding="utf-8")
    ast.parse(source)
    assert '"strength_5m"' in source
    assert '"execution_strength"' not in source
    assert "apply_rest_live_metric_values" in source


def test_new_worker_patches_have_valid_python_syntax_and_no_qax():
    for path in (
        TOP20_PATCH,
        PROVENANCE_PATCH,
        STRENGTH5_PATCH,
        MARKET_MANAGER_PATCH,
    ):
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        for forbidden in (
            "QAxWidget",
            "SetRealReg",
            "GetCommRealData",
            "dynamicCall",
            "Start-Process",
            "subprocess",
            "document.",
        ):
            assert forbidden not in source
