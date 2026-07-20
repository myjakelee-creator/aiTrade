from __future__ import annotations

from types import SimpleNamespace

import realtime_v2
from realtime_v2.html_execution_strength_label_patch import install as install_label
from realtime_v2.html_null_metric_patch import install as install_null_guard
from realtime_v2.html_realtime_candle_close_patch import (
    install as install_realtime_candle_close,
)


def test_missing_metrics_render_as_dash_and_execution_strength_uses_kiwoom_label(
    monkeypatch,
):
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(
        realtime_v2,
        "worker64_guarded_large",
        fake_large,
        raising=False,
    )

    install_null_guard()
    install_label()

    html = """<script>
function numeric(v){const n=Number(v);return Number.isFinite(n)?n:null;}
function clampNumber(v,min,max){const n=Number(v);return Number.isFinite(n)?Math.max(min,Math.min(max,n)):null;}
function fmtNum(v,d=0){const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d}):'-';}
function fmtRate(v){const n=Number(v);return Number.isFinite(n)?(n>=1000?`${Math.round(n).toLocaleString('en-US')}`:n.toFixed(n>=100?0:1)):'-';}
function fmtRankChange(v){const n=Number(v);if(!Number.isFinite(n))return'-';if(n>0)return`↑${Math.round(n)}`;return'0';}
function clsSigned(v){const n=Number(v);return!Number.isFinite(n)?'':n>0?'plus':n<0?'minus':'zero';}
function clsRatio(v){const n=Number(v);return!Number.isFinite(n)||n<=0?'zero':n>=1?'ratio-hot':'ratio-cold';}
function clsStrength(v){const n=Number(v);return!Number.isFinite(n)?'':n>=100?'strength-hot':'strength-cold';}
const column={key:'execution_strength',label:'순간강도'};
const status=`잔량비 숫자 · 순간 숫자 · 5분 숫자`;
</script>"""

    rendered = fake_large._ui_safety_patch(html)

    assert "STOCKBOARD_V2_NULL_METRIC_GUARD_20260716" in rendered
    assert "STOCKBOARD_V2_EXECUTION_STRENGTH_LABEL_20260716" in rendered
    assert "if(v===undefined||v===null||v==='')return null" in rendered
    assert "if(v===undefined||v===null||v==='')return '-'" in rendered
    assert "label:'체결강도'" in rendered
    assert "· 체결 숫자" in rendered
    assert "순간강도" not in rendered


def test_daily_candle_close_prefers_realtime_price_without_changing_ohl(monkeypatch):
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(
        realtime_v2,
        "worker64_guarded_large",
        fake_large,
        raising=False,
    )

    install_realtime_candle_close()

    html = """<script>
function pickOhlc(r){const o=r.ohlc||r.realtime_ohlc||r.display_ohlc||null;const open=numeric(o?.open??r.open??r.open_price??r.day_open),high=numeric(o?.high??r.high??r.high_price??r.day_high),low=numeric(o?.low??r.low??r.low_price??r.day_low),close=numeric(o?.close??o?.current??r.close??r.price??r.trade_price);if([open,high,low,close].some(v=>v===null)||high<=low)return null;return{open,high,low,close};}
</script>"""

    rendered = fake_large._ui_safety_patch(html)

    assert "STOCKBOARD_V2_REALTIME_CANDLE_CLOSE_20260716" in rendered
    assert "open=numeric(o?.open??r.open??r.open_price??r.day_open)" in rendered
    assert "high=numeric(o?.high??r.high??r.high_price??r.day_high)" in rendered
    assert "low=numeric(o?.low??r.low??r.low_price??r.day_low)" in rendered
    assert "close=numeric(r.price??r.trade_price??o?.current??o?.close??r.close)" in rendered
    assert "close=numeric(o?.close??o?.current??r.close??r.price??r.trade_price)" not in rendered
