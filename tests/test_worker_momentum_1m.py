from __future__ import annotations

import threading
from copy import deepcopy
from types import SimpleNamespace

import realtime_v2
import realtime_v2.html_momentum_badge_patch as html_patch
import realtime_v2.worker_momentum_1m_patch as momentum


def _candle(minute, *, open_price, high, low, close, vwap=None):
    return {
        "minute_key": minute,
        "minute_text": "09:00",
        "trading_date": "20260716",
        "phase": "regular",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "vwap": vwap,
        "partial": False,
    }


def test_vwap_scale_is_inferred_only_when_price_range_is_plausible():
    value, scale, quality = momentum.infer_vwap(
        cumulative_value_raw=100_000,
        cumulative_volume=1_000,
        trade_price=99_500,
    )

    assert value == 100_000
    assert scale == 1_000
    assert quality == "validated_inferred_scale"

    missing, _scale, reason = momentum.infer_vwap(1, 1_000_000, 99_500)
    assert missing is None
    assert reason == "scale_out_of_range"


def test_breakout_priority_precedes_support_and_uses_previous_vwap():
    previous = _candle(10, open_price=100, high=101, low=98, close=99, vwap=100)
    current = _candle(11, open_price=101, high=104, low=100, close=103, vwap=102)

    assert momentum.detect_vwap_signal(current, previous) == "가중돌파"
    assert momentum.detect_open_signal(current, previous, 100) == "시가돌파"


def test_support_resistance_and_strict_equality_rules():
    support = _candle(20, open_price=101, high=103, low=100, close=102, vwap=101)
    resistance = _candle(21, open_price=99, high=101, low=97, close=98, vwap=100)
    equality = _candle(22, open_price=100, high=101, low=99, close=100, vwap=100)

    assert momentum.detect_vwap_signal(support, None) == "가중지지"
    assert momentum.detect_vwap_signal(resistance, None) == "가중저항"
    assert momentum.detect_vwap_signal(equality, None) is None
    assert momentum.detect_open_signal(support, None, 101) == "시가지지"
    assert momentum.detect_open_signal(resistance, None, 99) == "시가저항"
    assert momentum.detect_open_signal(equality, None, 100) is None


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {
            "000001": {
                "stock_code": "000001",
                "rank": 1,
                "day_open": 100_000,
            }
        }
        self.daily_values_by_code = {}
        self.dirty = False
        self.rebuilds = []

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.dirty = True

    def request_background_rebuild(self, *, reason, force=False):
        self.rebuilds.append((reason, force))

    def stage_approved_trade_events(self, events):
        return None

    def rows(self, limit=300):
        return [deepcopy(value) for value in self.quotes.values()][:limit]

    def reset_approved_minute_pipeline_for_date(self, target, phase):
        return None


class FakeBase:
    State = FakeState
    DAILY_PERSIST_KEYS = ()


def _event(clock, price, vwap, volume):
    return {
        "stock_code": "000001",
        "execution_strength_source_time": clock,
        "execution_strength_trade_price": price,
        "cumulative_volume": volume,
        "cumulative_trade_value_raw": vwap * volume / 1_000,
    }


def test_installed_pipeline_builds_two_badges_from_completed_minutes(monkeypatch):
    day = 20260716
    mutable = {"minute": day * 1440 + 8 * 60 + 59}
    monkeypatch.setattr(
        momentum,
        "_session",
        lambda now=None: SimpleNamespace(
            trading_date="20260716",
            calendar_date="20260716",
            phase="regular",
            windows={"regular_start": "09:00"},
        ),
    )
    monkeypatch.setattr(momentum, "_expected_date", lambda now=None: "20260716")
    monkeypatch.setattr(momentum, "_system_minute_key", lambda now=None: mutable["minute"])

    class LocalState(FakeState):
        pass

    class LocalBase:
        State = LocalState
        DAILY_PERSIST_KEYS = ()

    momentum.install(LocalBase)
    state = LocalState()
    state.stage_approved_trade_events([
        _event("090005", 99_500, 100_000, 1_000),
        _event("090050", 99_000, 100_000, 2_000),
    ])
    state.stage_approved_trade_events([
        _event("090105", 100_500, 100_500, 3_000),
        _event("090150", 102_000, 101_000, 4_000),
    ])
    state.stage_approved_trade_events([_event("090205", 101_500, 101_200, 5_000)])

    daily = state.daily_values_by_code["000001"]
    assert daily["momentum_vwap_signal"] == "가중돌파"
    assert daily["momentum_open_signal"] == "시가돌파"
    assert state.status["momentum_1m_extra_qax_fids"] == 0
    assert state.status["momentum_1m_extra_rest_requests"] == 0
    assert state.status["momentum_1m_extra_websockets"] == 0

    mutable["minute"] = day * 1440 + 9 * 60 + 2
    row = state.rows()[0]
    assert [item["label"] for item in row["momentum_badges"]] == ["가중돌파", "시가돌파"]
    assert row["momentum_status"] == "live"
    assert ("momentum_1m_signal", False) in state.rebuilds
    assert "momentum_last_completed_candle" in LocalBase.DAILY_PERSIST_KEYS


def test_html_patch_replaces_grade_and_adds_global_alert_without_momentum_column(monkeypatch):
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(realtime_v2, "worker64_guarded_large", fake_large, raising=False)
    html_patch.install()

    html = """<html><head></head><body>
<div id="topbar" class="topbar"></div>
<div title="remove-me">x</div>
<script>
  const marketSupplyRow = document.getElementById('market-supply-row');
  function escapeHtml(v){return String(v??'');}
  function cellFlashClass(){return '';}
  function gradeHtml(r){const t=r.candidate_grade_text||r.grade_text||r.grade||r.candidate_grade||'-';const l=String(t).slice(0,1).toLowerCase();return`<span class="grade ${['a','b','c','d','f'].includes(l)?l:''}" title="score ${r.grade_score??'-'}">${escapeHtml(t)}</span>`;}
  function deriveClientFields(row){return row;}
  function rowHtml(raw){
    const r=deriveClientFields(raw); const code=String(r.stock_code||'');
    return `<tr><td class="center${cellFlashClass(code,'grade',r.candidate_grade_text||r.grade_text||r.grade||r.candidate_grade||'-')}">${gradeHtml(r)}</td><td><div class="mini-candle" title="${escapeHtml(candleTitle(r,o))}"></div></td></tr>`;
  }
  function render(payload){lastPayload=payload;markSortHeaders();}
  setInterval(()=>{clockEl.textContent='x';},500);
</script></body></html>"""

    rendered = fake_large._ui_safety_patch(html)
    assert "STOCKBOARD_V2_MOMENTUM_GRADE_ALERTS_20260717" in rendered
    assert "momentum-alert-strip" in rendered
    assert "momentumGradeHtml(r)" in rendered
    assert "stockboardMomentumAlternate" in rendered
    assert "label:'모멘텀'" not in rendered
    assert "momentum-cell" not in rendered
    assert 'title="remove-me"' not in rendered
    assert 'title="score ' not in rendered
    assert 'title="${escapeHtml(candleTitle(r,o))}"' in rendered
