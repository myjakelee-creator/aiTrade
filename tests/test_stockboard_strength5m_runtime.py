from __future__ import annotations

from threading import RLock
from types import SimpleNamespace

import realtime_v2.collector32_large as collector_large
import realtime_v2.strength5m_scheduler as scheduler_module
from realtime_v2.strength5m_scheduler import Strength5mScheduler, build_lane_plan
from realtime_v2.worker64_guarded_large import _five_min_strength_display_patch, base


class DummyBase:
    @staticmethod
    def normalize_code(value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:6] if len(digits) >= 6 else ""


class DummyProvider:
    def __init__(self):
        self._lock = RLock()
        self._strength_probe_inflight = None
        self._orderbook_probe_inflight = None
        self._opt10055_probe_inflight = None
        self._strength_probe_pending = []
        self._orderbook_probe_pending = []
        self._opt10055_probe_pending = []
        self._close_metrics_queue = []
        self._strength_probe_last_request_at = 0.0
        self._orderbook_probe_last_request_at = 0.0
        self._opt10055_probe_last_request_at = 0.0
        self._close_metrics_last_request_at = 0.0


def _payload(count=60):
    return {
        "rows": [
            {
                "stock_code": f"{index:06d}",
                "model_rank": index,
                "strength_5m": 100 + index,
            }
            for index in range(1, count + 1)
        ]
    }


def test_lane_plan_only_classifies_query_priority_without_reordering_rows():
    plan = build_lane_plan(DummyBase, _payload(), selected="000060")
    codes = [item["stock_code"] for item in plan]
    lanes = [item["lane"] for item in plan]

    assert codes[0] == "000060"
    assert lanes[0] == "s1"
    assert [item["stock_code"] for item in plan if item["lane"] == "top20"] == [
        f"{index:06d}" for index in range(1, 21)
    ]
    assert [item["stock_code"] for item in plan if item["lane"] == "hidden50"] == [
        f"{index:06d}" for index in range(21, 51)
    ]
    assert len(codes) == len(set(codes)) == 60


def test_scheduler_uses_slower_intervals_for_lower_priority_lanes():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    normal = scheduler.NORMAL
    opening = scheduler.OPENING

    assert normal["s1"] < normal["top20"] < normal["hidden50"] < normal["top300"]
    assert opening["s1"] < opening["top20"] < opening["hidden50"] < opening["top300"]
    assert all(opening[key] >= normal[key] for key in normal)


def test_scheduler_stops_when_another_query_queue_is_busy():
    provider = DummyProvider()
    scheduler = Strength5mScheduler(DummyBase, provider)
    assert scheduler._idle() is True

    provider._orderbook_probe_pending.append({"stock_code": "000001"})
    assert scheduler._idle() is False
    provider._orderbook_probe_pending.clear()

    provider._opt10055_probe_inflight = {"stock_code": "000002"}
    assert scheduler._idle() is False


def test_calendar_blocks_before_market_and_allows_premarket(monkeypatch):
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())

    monkeypatch.setattr(
        scheduler_module,
        "market_session_now",
        lambda: SimpleNamespace(
            phase="before_market",
            is_trading_day=True,
            accept_realtime=False,
            windows={"regular_start": "09:00"},
        ),
    )
    assert scheduler._market_accepts_strength_query() is False

    monkeypatch.setattr(
        scheduler_module,
        "market_session_now",
        lambda: SimpleNamespace(
            phase="premarket",
            is_trading_day=True,
            accept_realtime=True,
            windows={"regular_start": "09:00"},
        ),
    )
    assert scheduler._market_accepts_strength_query() is True


def test_served_html_uses_worker_strength5m_and_disables_client_calculation():
    source = """
    <span>잔량비/순간강도/1분강도 클릭=숫자↔배경</span>
    <script>
    const metricKeys = new Set(['bid_ask_ratio', 'execution_strength', 'strength_1m']);
    const columns = [{ key:'strength_1m', label:'1분강도', className:'num metric-header', sort:'strength_1m', width:70, min:54 }];
    const COLUMN_MINIMIZE_MAX = { strength_1m: 58, };
    function deriveClientFields(row){ return {calculated:true}; }
    function rowHtml(r){
      const one=numeric(r.strength_1m);
      return `${metricCell('strength_1m',one,fmtStrength(one),clsStrength(one)+cellFlashClass(code,'strength_1m',one),'old')}`;
    }
    clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();
    </script>
    """
    result = _five_min_strength_display_patch(source)

    assert "5분강도" in result
    assert "strength_5m" in result
    assert "metricCell('strength_1m'" not in result
    assert "return row && typeof row === 'object' ? {...row} : {};" in result


def test_zero_or_blank_strength_never_replaces_last_valid_value():
    zero, zero_valid = collector_large._sanitize_close_metrics(
        {"strength_5m": 0, "strength_20m": 0, "strength_status": "ok"}
    )
    blank, blank_valid = collector_large._sanitize_close_metrics(
        {"strength_5m": "", "strength_status": "error"}
    )
    valid, is_valid = collector_large._sanitize_close_metrics(
        {"strength_5m": "123.45", "strength_status": "ok"}
    )

    assert zero_valid is False and "strength_5m" not in zero
    assert blank_valid is False and "strength_5m" not in blank
    assert is_valid is True and valid["strength_5m"] == 123.45


def test_strength_snapshot_is_same_trading_day_only(tmp_path, monkeypatch):
    path = tmp_path / "strength_snapshot.json"
    monkeypatch.setattr(collector_large, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(collector_large, "trading_date_text", lambda: "20260710")

    collector_large._persist_last_valid_strength(
        "000001",
        {
            "strength_5m": 130,
            "strength_status": "ok",
            "strength_snapshot_at": "2026-07-10T10:00:00",
        },
    )

    assert collector_large._load_strength_snapshot_values("20260710")["000001"]["strength_5m"] == 130
    assert collector_large._load_strength_snapshot_values("20260711") == {}


def test_worker_persists_last_valid_five_minute_strength_for_restart():
    assert "strength_5m" in base.DAILY_PERSIST_KEYS
    assert "strength_snapshot_at" in base.DAILY_PERSIST_KEYS
    assert "strength_status" in base.DAILY_PERSIST_KEYS
