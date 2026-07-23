from __future__ import annotations

"""Fail closed on implausible amount ratios and remove misleading event-age health.

The amount-ratio denominator is populated from the previous completed ka10086 daily
row. A legacy cache can contain a unit-mismatched value; those rows must not display
or score a multi-million-percent ratio. This output guard requires an older dated
previous value and rejects ratios above a deliberately generous 1000x ceiling until
the cross-checked cache is rebuilt.

The connection badge previously treated ``last_event_at`` as whole-pipeline latency.
That timestamp can represent an older individual event even while QAx, Sender, Worker,
and the dedicated price SSE are healthy. The badge now reports only connection state;
transport latency remains visible as the separate ``price ... · full ...`` label.

No QAx, FID, Collector, EventSender, SSE cadence, ranking, orderbook, or metric source
is added or changed.
"""

from copy import deepcopy
from typing import Any

PATCH_VERSION = "amount_ratio_date_guard_v1"
MAX_VALID_AMOUNT_RATIO = 1000.0
_UI_MARKER = "STOCKBOARD_V2_CONNECTION_HEALTH_TRUTH_20260723"
_UI_ANCHOR = (
    "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});"
    "loadCandidateModels();loadContext();markSortHeaders();connectStream();"
)

_UI_PATCH = r"""
  /* STOCKBOARD_V2_CONNECTION_HEALTH_TRUTH_20260723 */
  __sbv2UpdateConnectionHealth = function(payload, mode, phase){
    const st = (payload && payload.status) || {};
    const collector = st.collector_status || {};
    const sender = collector.sender_stats || {};
    const provider = collector.status || {};
    const providerStarted = collector.provider_started === true;
    const registered = Number(provider.realreg_code_count || collector.registered_count || 0);
    const realreg = provider.realreg_succeeded === true || registered > 0;
    const login = String(provider.login_state || '');
    const senderConnected = sender.connected === true;
    const eventCount = Number(st.event_count || 0);
    const tradeCount = Number(st.trade_count || 0);
    const phaseText = phase ? ` · ${phase}` : '';
    let label = '';
    let cls = 'badge bad';
    if(!providerStarted || login === 'requested' || !realreg || registered <= 0){
      label = `연결 대기${phaseText}`;
    }else if(login && login !== 'connected'){
      label = `로그인 확인${phaseText}`;
    }else if(!senderConnected){
      label = `collector 끊김${phaseText}`;
    }else if(eventCount <= 0 && tradeCount <= 0){
      label = `수신 대기${phaseText}`;
      cls = 'badge warn';
    }else{
      label = `연결 OK${phaseText}`;
      cls = mode === 'stream' ? 'badge ok' : 'badge warn';
    }
    statusEl.textContent = label;
    statusEl.className = cls;
  };
"""


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _current_date(state) -> str:
    status = getattr(state, "status", {})
    if not isinstance(status, dict):
        return ""
    for key in (
        "market_trading_date",
        "board_display_current_trading_date",
        "board_expected_trading_date",
    ):
        value = _date_digits(status.get(key))
        if value:
            return value
    return ""


def validate_amount_ratio(row: dict[str, Any], current_date: str) -> dict[str, Any]:
    result = deepcopy(row)
    current = _number(result.get("trade_value_eok"))
    previous = _number(
        result.get("prev_trade_value_eok")
        or result.get("previous_trade_value_eok")
        or result.get("yesterday_trade_value_eok")
    )
    previous_date = _date_digits(result.get("prev_trade_value_date"))

    reason = None
    ratio = None
    if current is None or current < 0:
        reason = "current_trade_value_missing"
    elif previous is None or previous <= 0:
        reason = "previous_trade_value_missing"
    elif not current_date or not previous_date or previous_date >= current_date:
        reason = "previous_trade_value_date_unverified"
    else:
        ratio = current / previous
        if ratio < 0 or ratio > MAX_VALID_AMOUNT_RATIO:
            reason = "previous_trade_value_unit_mismatch_suspected"
            ratio = None

    result["amount_ratio"] = round(ratio, 6) if ratio is not None else None
    result["amount_ratio_guard_version"] = PATCH_VERSION
    result["amount_ratio_missing_reason"] = reason
    return result


def _install_rows(base) -> None:
    state_class = getattr(base, "State", None)
    if (
        state_class is None
        or not callable(getattr(state_class, "rows", None))
        or getattr(state_class, "_stockboard_amount_ratio_guard_installed", False)
    ):
        return

    original_rows = state_class.rows

    def rows(self, *args, **kwargs):
        raw_rows = original_rows(self, *args, **kwargs)
        if not isinstance(raw_rows, list):
            return raw_rows
        current_date = _current_date(self)
        guarded = [
            validate_amount_ratio(row, current_date) if isinstance(row, dict) else row
            for row in raw_rows
        ]
        invalid_count = sum(
            isinstance(row, dict) and row.get("amount_ratio") is None for row in guarded
        )
        with self.lock:
            self.status["amount_ratio_guard_version"] = PATCH_VERSION
            self.status["amount_ratio_guard_invalid_count"] = invalid_count
        return guarded

    state_class.rows = rows
    state_class._stockboard_amount_ratio_guard_installed = True
    state_class._stockboard_amount_ratio_guard_version = PATCH_VERSION


def _install_ui(large) -> None:
    if large is None or getattr(large, "_stockboard_connection_health_truth_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if _UI_MARKER in patched or _UI_ANCHOR not in patched:
            return patched
        return patched.replace(_UI_ANCHOR, f"{_UI_PATCH}\n{_UI_ANCHOR}", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._stockboard_connection_health_truth_installed = True


def install(base, large=None) -> None:
    _install_rows(base)
    _install_ui(large)


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_ratio_connection_cleanup_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening_cache(base) -> None:
        original_install(base)
        from realtime_v2 import worker64_guarded_large as large

        install(base, large)

    opening_module.install = install_after_opening_cache
    opening_module._ratio_connection_cleanup_install_wrapped = True
