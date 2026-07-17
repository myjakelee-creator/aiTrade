from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "approved_minute_rollover_guard_v3"
COMPLETE_START_PHASES = {"premarket", "opening_call"}
NON_TRADING_HOLD_GROUPS = ("orderbook", "execution", "strength5")


def _date_digits(value) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _is_trading_session(session: Any) -> bool:
    explicit = getattr(session, "is_trading_day", None)
    if explicit is not None:
        return bool(explicit)
    return str(getattr(session, "phase", "") or "") not in {"weekend", "holiday"}


def _first_present(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None


def _first_positive(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        number = to_number(value)
        if number is not None and float(number) > 0:
            return value
    return None


def _approved_ui_fallback(source: dict[str, Any]) -> dict[str, Any]:
    values = dict(source)
    aliases = {
        "bid_ask_ratio": ("ui_bid_ask_ratio", "last_valid_bid_ask_ratio"),
        "bid_pct": ("ui_bid_pct", "last_valid_bid_pct"),
        "ask_pct": ("ui_ask_pct", "last_valid_ask_pct"),
        "bid_volume": ("ui_bid_volume", "last_valid_bid_volume"),
        "ask_volume": ("ui_ask_volume", "last_valid_ask_volume"),
        "best_ask_price": ("ui_best_ask_price",),
        "best_bid_price": ("ui_best_bid_price",),
        "orderbook_received_at": (
            "ui_orderbook_observed_at",
            "last_valid_orderbook_at",
        ),
        "orderbook_source_trading_date": ("ui_orderbook_source_trading_date",),
        "execution_strength": (
            "ui_execution_strength",
            "last_valid_execution_strength",
        ),
        "execution_strength_received_at": (
            "ui_execution_strength_observed_at",
            "last_valid_strength_at",
        ),
        "execution_source_trading_date": ("ui_execution_source_trading_date",),
        "strength_5m": ("ui_strength_5m", "last_valid_strength_5m"),
        "strength_20m": ("ui_strength_20m",),
        "strength_60m": ("ui_strength_60m",),
        "strength_snapshot_at": (
            "ui_strength_observed_at",
            "last_valid_strength_at",
        ),
        "strength_source_trading_date": ("ui_strength_source_trading_date",),
    }
    positive_targets = {"bid_ask_ratio", "execution_strength", "strength_5m"}
    for target, fallbacks in aliases.items():
        current = to_number(values.get(target)) if target in positive_targets else None
        missing = values.get(target) in (None, "") or (
            target in positive_targets and (current is None or float(current) <= 0)
        )
        if missing:
            fallback = (
                _first_positive(values, *fallbacks)
                if target in positive_targets
                else _first_present(values, *fallbacks)
            )
            if fallback not in (None, ""):
                values[target] = fallback
    if values.get("bid_ask_ratio") not in (None, ""):
        values.setdefault("orderbook_source", "kiwoom_rest_ws_0D_rotating")
        values.setdefault("orderbook_status", "published_60s_last_good")
    if values.get("execution_strength") not in (None, ""):
        values.setdefault("execution_strength_source", "kiwoom_rest_ws_0B_fid228")
        values.setdefault("execution_strength_status", "published_60s_last_good")
    if values.get("strength_5m") not in (None, ""):
        values.setdefault("strength_source", "ka10046_rest_lowload")
        values.setdefault("strength_status", "published_minute_batch_last_good")
    return values


def _exact_previous_daily_values(lifecycle, expected: str) -> dict[str, dict[str, Any]]:
    expected = _date_digits(expected)
    if not expected:
        return {}
    try:
        payload = lifecycle._read_json(
            lifecycle.RUNTIME_DIR / f"daily_state_{expected}.json"
        )
    except (AttributeError, OSError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    payload_date = _date_digits(
        payload.get("trading_date") or payload.get("source_trading_date")
    )
    if payload_date and payload_date != expected:
        return {}
    raw_codes = payload.get("codes")
    if not isinstance(raw_codes, dict):
        raw_codes = payload.get("values")
    if not isinstance(raw_codes, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_code, raw_values in raw_codes.items():
        code = normalize_code(raw_code)
        if code and isinstance(raw_values, dict):
            result[code] = dict(raw_values)
    return result


def _merge_metric_sources(*sources: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            result.update(source)
    return result


def _non_trading_entry(
    lifecycle,
    entry,
    *,
    code: str,
    group: str,
    expected: str,
    now: datetime,
):
    if isinstance(entry, dict):
        values = entry.get("values")
        if (
            str(entry.get("group") or "") == group
            and _date_digits(entry.get("source_trading_date")) == _date_digits(expected)
            and isinstance(values, dict)
            and lifecycle._group_usable(values, group)
        ):
            rebased = dict(entry)
            rebased["expires_at"] = lifecycle._next_premarket_boundary(now).isoformat(
                timespec="seconds"
            )
            return rebased, rebased.get("expires_at") != entry.get("expires_at")
    return None, False


def _restore_non_trading_hold(self, result, session, now: datetime) -> tuple[int, int]:
    if not isinstance(result, list):
        return 0, 0

    import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

    expected = lifecycle._expected_date(session, now)
    cache = getattr(self, "six_metric_lifecycle_by_group", None)
    cache = cache if isinstance(cache, dict) else {}
    codes = {
        normalize_code(row.get("stock_code"))
        for row in result
        if isinstance(row, dict) and normalize_code(row.get("stock_code"))
    }
    exact_previous = _exact_previous_daily_values(lifecycle, expected)
    prior_display = getattr(self, "previous_daily_display_values_by_code", None)
    prior_display = prior_display if isinstance(prior_display, dict) else {}
    prior_metric = getattr(self, "_previous_daily_display_cache", None)
    prior_metric = prior_metric if isinstance(prior_metric, dict) else {}
    row_by_code = {
        normalize_code(row.get("stock_code")): row
        for row in result
        if isinstance(row, dict) and normalize_code(row.get("stock_code"))
    }

    with self.lock:
        source_by_code = {
            code: _approved_ui_fallback(
                _merge_metric_sources(
                    exact_previous.get(code),
                    prior_display.get(code),
                    prior_metric.get(code),
                    row_by_code.get(code),
                    getattr(self, "daily_values_by_code", {}).get(code),
                    getattr(self, "quotes", {}).get(code),
                )
            )
            for code in codes
        }

    for values in source_by_code.values():
        if not isinstance(values, dict):
            continue
        if values.get("bid_ask_ratio") not in (None, ""):
            values.setdefault("orderbook_source_trading_date", expected)
            values.setdefault("_session_hold_orderbook_date", expected)
        if values.get("execution_strength") not in (None, ""):
            values.setdefault("execution_source_trading_date", expected)
            values.setdefault("_session_hold_execution_date", expected)
        if values.get("strength_5m") not in (None, ""):
            values.setdefault("strength_source_trading_date", expected)
            values.setdefault("_session_hold_strength5_date", expected)

    restored = 0
    rebased_count = 0
    previous_daily_used = 0
    for row in result:
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get("stock_code"))
        if not code:
            continue
        used_previous_for_code = False
        for group in NON_TRADING_HOLD_GROUPS:
            group_cache = cache.setdefault(group, {})
            raw_entry = group_cache.get(code) if isinstance(group_cache, dict) else None
            entry, rebased = _non_trading_entry(
                lifecycle,
                raw_entry,
                code=code,
                group=group,
                expected=expected,
                now=now,
            )
            if entry is None:
                entry = lifecycle._entry_from_values(
                    code,
                    group,
                    source_by_code.get(code, {}),
                    source_date=expected,
                    now=now,
                )
                entry, rebased = _non_trading_entry(
                    lifecycle,
                    entry,
                    code=code,
                    group=group,
                    expected=expected,
                    now=now,
                )
                used_previous_for_code = used_previous_for_code or (
                    code in exact_previous
                    or code in prior_display
                    or code in prior_metric
                )
            if entry is None:
                continue
            if isinstance(group_cache, dict) and group_cache.get(code) != entry:
                group_cache[code] = entry
                self.six_metric_lifecycle_dirty = True
            lifecycle._overlay_entry(
                row,
                group,
                entry,
                phase=str(getattr(session, "phase", "") or "holiday"),
            )
            restored += 1
            rebased_count += int(bool(rebased))
        previous_daily_used += int(used_previous_for_code)

    with self.lock:
        self.status["approved_non_trading_exact_daily_count"] = len(exact_previous)
        self.status["approved_non_trading_previous_daily_used_count"] = previous_daily_used
    return restored, rebased_count


def install(base) -> None:
    """Protect approved minute state across non-trading weekdays and real rollover.

    Holiday/weekend rows retain the verified previous-session orderbook, execution
    strength and five-minute strength lifecycle entries. If lifecycle entries are
    incomplete after moving to another PC, the exact previous trading day's daily-state
    file supplies approved UI aliases and last-valid values. Non-trading sessions do not
    publish a new minute, clear intraday accumulators, or downgrade large-trade quality.
    The next real premarket still performs the original one-time rollover.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_approved_minute_rollover_guard_installed",
        False,
    ):
        return

    original_state_init = state_class.__init__
    original_ensure = getattr(state_class, "ensure_metric_session_state_date", None)
    original_stage_trade = getattr(state_class, "stage_approved_trade_events", None)
    original_publish = getattr(state_class, "publish_approved_minute_metrics", None)
    original_rows = state_class.rows

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        session = market_session_now(datetime.now())
        target = _date_digits(
            self.status.get("metric_session_state_date")
            or getattr(session, "trading_date", "")
            or getattr(session, "calendar_date", "")
        )
        phase = str(getattr(session, "phase", "") or "")
        trading_session = _is_trading_session(session)
        restored = int(self.status.get("approved_large_checkpoint_restored_count") or 0)
        self._approved_pipeline_date = target
        self._approved_large_full_session_coverage = (
            trading_session and phase in COMPLETE_START_PHASES and restored == 0
        )
        with self.lock:
            self.status["approved_minute_rollover_guard_installed"] = True
            self.status["approved_minute_rollover_guard_version"] = PATCH_VERSION
            self.status["approved_pipeline_trading_date"] = target or None
            self.status["approved_pipeline_non_trading_hold"] = not trading_session
            self.status["approved_large_full_session_coverage"] = bool(
                self._approved_large_full_session_coverage
            )
            if trading_session and not self._approved_large_full_session_coverage:
                for live in self._approved_large_live.values():
                    if isinstance(live, dict):
                        live["quality"] = "GAP_POSSIBLE"

    def reset_for_date(self, target: str, phase: str) -> None:
        with self.lock:
            self._approved_execution_stage.clear()
            self._approved_orderbook_stage.clear()
            self._approved_strength_stage.clear()
            self._approved_large_live.clear()
            self._approved_large_seen.clear()
            self._approved_trade_value_last.clear()
            self._approved_trade_value_buckets.clear()
            self._approved_trade_value_partial.clear()
            self._approved_last_publish_minute = int(time.time() // 60)
            self._approved_pipeline_date = target
            self._approved_large_full_session_coverage = phase in COMPLETE_START_PHASES
            self.status["approved_pipeline_trading_date"] = target or None
            self.status["approved_pipeline_rollover_at"] = __import__(
                "realtime_v2.common", fromlist=["now_text"]
            ).now_text()
            self.status["approved_pipeline_rollover_count"] = int(
                self.status.get("approved_pipeline_rollover_count") or 0
            ) + 1
            self.status["approved_large_full_session_coverage"] = bool(
                self._approved_large_full_session_coverage
            )

    def ensure_date(self, *args, **kwargs):
        session = market_session_now(datetime.now())
        target = original_ensure(self, *args, **kwargs) if callable(original_ensure) else ""
        target = _date_digits(target)
        previous = _date_digits(getattr(self, "_approved_pipeline_date", ""))
        if not _is_trading_session(session):
            if target:
                self._approved_pipeline_date = target
            with self.lock:
                self.status["approved_pipeline_non_trading_hold"] = True
                self.status["approved_pipeline_rollover_suppressed_non_trading"] = int(
                    self.status.get("approved_pipeline_rollover_suppressed_non_trading") or 0
                ) + 1
                self.status["approved_pipeline_trading_date"] = target or previous or None
            return target or previous
        if target and target != previous:
            phase = str(getattr(session, "phase", "") or "")
            final_reset = getattr(self, "reset_approved_minute_pipeline_for_date", None)
            if callable(final_reset):
                final_reset(target, phase)
            else:
                reset_for_date(self, target, phase)
        return target

    def rows(self, limit: int = 300):
        if callable(original_ensure):
            ensure_date(self)
        result = original_rows(self, limit)
        now = datetime.now()
        session = market_session_now(now)
        restored = 0
        rebased = 0
        if not _is_trading_session(session):
            restored, rebased = _restore_non_trading_hold(self, result, session, now)
        with self.lock:
            self.status["approved_pipeline_non_trading_hold"] = not _is_trading_session(session)
            self.status["approved_non_trading_hold_restored_group_count"] = restored
            self.status["approved_non_trading_hold_rebased_entry_count"] = rebased
        return result

    def stage_trade_events(self, events):
        if callable(original_ensure):
            ensure_date(self)
        result = original_stage_trade(self, events) if callable(original_stage_trade) else None
        session = market_session_now(datetime.now())
        if not _is_trading_session(session):
            return result
        if not bool(getattr(self, "_approved_large_full_session_coverage", False)):
            with self.lock:
                for live in self._approved_large_live.values():
                    if isinstance(live, dict):
                        live["quality"] = "GAP_POSSIBLE"
                self.status["approved_large_full_session_coverage"] = False
        return result

    def publish(self, force: bool = False):
        session = market_session_now(datetime.now())
        if not _is_trading_session(session):
            with self.lock:
                self.status["approved_minute_publish_suppressed_non_trading"] = int(
                    self.status.get("approved_minute_publish_suppressed_non_trading") or 0
                ) + 1
                self.status["approved_minute_publish_suppressed_phase"] = str(
                    getattr(session, "phase", "") or ""
                )
            return False
        return original_publish(self, force) if callable(original_publish) else False

    state_class.__init__ = state_init
    state_class.reset_approved_minute_pipeline_for_date = reset_for_date
    if callable(original_ensure):
        state_class.ensure_metric_session_state_date = ensure_date
    state_class.rows = rows
    if callable(original_stage_trade):
        state_class.stage_approved_trade_events = stage_trade_events
    if callable(original_publish):
        state_class.publish_approved_minute_metrics = publish
    state_class._stockboard_approved_minute_rollover_guard_installed = True

    if callable(getattr(state_class, "_quote", None)):
        from realtime_v2.worker_momentum_1m_patch import install as install_momentum_1m
        from realtime_v2.worker_minute_value_hold_patch import (
            install as install_minute_value_hold,
        )

        install_momentum_1m(base)
        install_minute_value_hold(base)
