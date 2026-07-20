from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from realtime_v2.common import RUNTIME_DIR, normalize_code, now_text, to_number
from realtime_v2.market_session import last_completed_trading_date, market_session_now

PATCH_VERSION = "market_metric_session_manager_v1"
CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}
METRICS = ("bidask", "strength", "large_trade")
METRIC_PRIORITY = {"bidask": 3, "strength": 2, "large_trade": 1}
LANE_PRIORITY = {"s1": 3, "top20": 2, "top100": 1}


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _clock_minutes(value: Any, fallback: str) -> int:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def market_metric_phase(now: datetime | None = None, config: dict[str, Any] | None = None) -> str:
    current = now or datetime.now()
    session = market_session_now(current)
    phase = str(session.phase or "outside")
    if phase != "regular":
        return phase

    manager = (config or {}).get("session_manager")
    manager = manager if isinstance(manager, dict) else {}
    opening_minutes = max(0, int(manager.get("opening_burst_minutes") or 10))
    windows = session.windows if isinstance(session.windows, dict) else {}
    regular_start_minute = _clock_minutes(windows.get("regular_start"), "09:00")
    current_minute = current.hour * 60 + current.minute
    if regular_start_minute <= current_minute < regular_start_minute + opening_minutes:
        return "opening_burst"
    return "regular"


def metric_target_trading_date(now: datetime | None = None) -> str:
    current = now or datetime.now()
    session = market_session_now(current)
    phase = str(session.phase or "")
    if phase in CLOSED_PHASES:
        return _date_digits(last_completed_trading_date(current))
    return _date_digits(session.trading_date or session.calendar_date)


def _policy(config: dict[str, Any], phase: str) -> dict[str, Any]:
    manager = config.get("session_manager")
    manager = manager if isinstance(manager, dict) else {}
    policies = manager.get("phase_policies")
    policies = policies if isinstance(policies, dict) else {}
    value = policies.get(phase)
    return dict(value) if isinstance(value, dict) else {"active": False, "scope": 0}


def _metric_date(values: dict[str, Any], metric: str) -> str:
    if metric == "bidask":
        keys = (
            "orderbook_source_trading_date",
            "_session_hold_orderbook_date",
            "orderbook_received_at",
            "last_valid_orderbook_at",
        )
    elif metric == "strength":
        keys = (
            "strength_source_trading_date",
            "_session_hold_strength5_date",
            "strength_snapshot_at",
            "last_valid_strength_at",
        )
    else:
        keys = (
            "large_trade_source_trading_date",
            "large_trade_trading_date",
            "_session_hold_large_trade_date",
            "large_trade_updated_at",
        )
    for key in keys:
        date_text = _date_digits(values.get(key))
        if date_text:
            return date_text
    return ""


def _metric_complete(values: dict[str, Any], metric: str, expected_date: str) -> bool:
    if not expected_date or _metric_date(values, metric) != expected_date:
        return False
    if metric == "bidask":
        ratio = _number(values.get("bid_ask_ratio"))
        return ratio is not None and ratio > 0
    if metric == "strength":
        value = _number(values.get("strength_5m"))
        return value is not None and value > 0
    source = str(values.get("large_trade_source") or "")
    status = str(values.get("large_trade_status") or "")
    count = _number(values.get("large_trade_net_count"))
    return count is not None and bool(source or status)


def _rank_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    code, row = item
    for key in ("candidate_rank", "model_rank", "pool_rank", "rank"):
        rank = _number(row.get(key))
        if rank is not None and rank > 0:
            return (0, int(rank), -(_number(row.get("trade_value_eok")) or 0.0), code)
    return (1, 999999, -(_number(row.get("trade_value_eok")) or 0.0), code)


def _daily_path_date(path: Any) -> str:
    name = Path(str(path or "")).stem
    return _date_digits(name)


def install(base) -> None:
    """Install one calendar-driven owner for metric scheduling and trading-day rollover.

    The existing REST updater thread is reused. No QAx registration, realtime FID,
    price callback, browser-side calculation, or additional worker thread is added.
    """

    import realtime_v2.worker_rest_live_metrics_patch as rest_module
    import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

    state_class = getattr(base, "State", None)
    updater_class = rest_module.RestLiveMetricUpdater
    if state_class is None or getattr(updater_class, "_stockboard_market_session_manager_installed", False):
        return

    original_state_init = state_class.__init__
    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    lifecycle_keys = set()
    for group in lifecycle.GROUPS.values():
        lifecycle_keys.update(group.get("display_keys") or ())
        lifecycle_keys.update(group.get("capture_keys") or ())
        lifecycle_keys.update(group.get("date_keys") or ())
    lifecycle_keys.update(
        {
            "amount_ratio_available",
            "orderbook_available",
            "execution_available",
            "strength5_available",
            "program_available",
            "large_trade_available",
        }
    )

    def load_state_for_path(self, path: Path) -> dict[str, dict[str, Any]]:
        previous_path = self.daily_state_path
        try:
            self.daily_state_path = path
            loaded = self._load_daily_state()
            return loaded if isinstance(loaded, dict) else {}
        finally:
            self.daily_state_path = previous_path

    def align_state_date(self, *, force: bool = False, initializing: bool = False) -> str:
        now_mono = time.monotonic()
        last_check = float(getattr(self, "_metric_session_last_rollover_check_mono", 0.0) or 0.0)
        if not force and now_mono - last_check < 1.0:
            return str(getattr(self, "_metric_session_state_date", "") or "")
        self._metric_session_last_rollover_check_mono = now_mono

        target_date = metric_target_trading_date()
        if not target_date:
            return ""
        current_date = str(getattr(self, "_metric_session_state_date", "") or "")
        if not current_date:
            current_date = _daily_path_date(getattr(self, "daily_state_path", ""))
        if current_date == target_date:
            with self.lock:
                self.status["metric_session_state_date"] = target_date
            self._metric_session_state_date = target_date
            return target_date

        previous_trade_values: dict[str, float] = {}
        previous_ranks: dict[str, int] = {}
        with self.lock:
            for code, quote in self.quotes.items():
                if not isinstance(quote, dict):
                    continue
                trade_value = _number(quote.get("trade_value_eok"))
                rank = _number(quote.get("rank"))
                if trade_value is not None and trade_value > 0:
                    previous_trade_values[code] = trade_value
                if rank is not None and rank > 0:
                    previous_ranks[code] = int(rank)

        if current_date and not initializing:
            try:
                original_persist(self, True)
            except Exception as error:
                with self.lock:
                    self.status["metric_session_rollover_persist_error"] = (
                        f"{type(error).__name__}: {error}"
                    )

        target_path = RUNTIME_DIR / f"daily_state_{target_date}.json"
        loaded = load_state_for_path(self, target_path)
        with self.lock:
            self.daily_state_path = target_path
            self.daily_values_by_code = loaded
            self.daily_dirty = False

            for code, quote in self.quotes.items():
                if not isinstance(quote, dict):
                    continue
                for key in lifecycle_keys:
                    quote.pop(key, None)
                entry = loaded.get(code)
                if isinstance(entry, dict):
                    for key, value in entry.items():
                        if key in lifecycle_keys and value not in (None, ""):
                            quote[key] = deepcopy(value)
                previous_value = previous_trade_values.get(code)
                if previous_value is not None and current_date != target_date:
                    quote["prev_trade_value_eok"] = previous_value
                    self.prev_trade_value_by_code[code] = previous_value
                previous_rank = previous_ranks.get(code)
                if previous_rank is not None and current_date != target_date:
                    quote["prev_rank"] = previous_rank
                    self.prev_rank_by_code[code] = previous_rank

            self._metric_session_state_date = target_date
            self.status["daily_state_path"] = str(target_path)
            self.status["daily_state_loaded_count"] = len(loaded)
            self.status["metric_session_state_date"] = target_date
            self.status["metric_session_previous_state_date"] = current_date or None
            self.status["metric_session_rollover_at"] = now_text()
            self.status["metric_session_rollover_count"] = int(
                self.status.get("metric_session_rollover_count") or 0
            ) + 1
            self.status["metric_session_rollover_persist_error"] = None
        return target_date

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        self._metric_session_last_rollover_check_mono = 0.0
        self._metric_session_state_date = ""
        with self.lock:
            self.status["market_metric_session_manager_installed"] = True
            self.status["market_metric_session_manager_version"] = PATCH_VERSION
        align_state_date(self, force=True, initializing=True)

    def rows(self, limit: int = 300):
        align_state_date(self)
        return original_rows(self, limit)

    state_class.__init__ = state_init
    state_class.rows = rows
    state_class.ensure_metric_session_state_date = align_state_date

    def wrap_state_apply(name: str) -> None:
        original = getattr(state_class, name, None)
        if not callable(original):
            return

        def wrapped(self, *args, __original: Callable = original, **kwargs):
            align_state_date(self)
            return __original(self, *args, **kwargs)

        setattr(state_class, name, wrapped)

    for method_name in (
        "apply_program_net_values",
        "apply_rest_live_metric_values",
        "apply_realtime_strength_ws",
        "apply_realtime_strength_ws_batch",
        "apply_large_trade_stage4_snapshot",
        "apply_rest_large_trade_delta",
    ):
        wrap_state_apply(method_name)

    def session_phase(self) -> str:
        return market_metric_phase(config=self.config)

    def phase_policy(self) -> dict[str, Any]:
        return _policy(self.config, session_phase(self))

    def active_session(self) -> bool:
        policy = phase_policy(self)
        return bool(policy.get("active")) and int(policy.get("scope") or 0) > 0

    def top_codes(self) -> list[str]:
        self._refresh_selected()
        policy = phase_policy(self)
        manager = self.config.get("session_manager")
        manager = manager if isinstance(manager, dict) else {}
        scope = int(policy.get("scope") or manager.get("scope_max_codes") or 100)
        scope = max(0, min(100, scope))
        with self.state.lock:
            rows = {
                normalize_code(code): dict(quote)
                for code, quote in self.state.quotes.items()
                if isinstance(quote, dict) and normalize_code(code)
            }
        ranked = sorted(rows.items(), key=_rank_key)
        codes = [code for code, _row in ranked[:scope]]
        selected = normalize_code(getattr(self, "selected_code", ""))
        if selected and selected in rows and selected not in codes and scope > 0:
            codes.insert(0, selected)
            codes = codes[:scope]
        with self.state.lock:
            self.state.status["metric_session_scope"] = scope
            self.state.status["metric_session_scope_code_count"] = len(codes)
        return codes

    def lane_for(self, code: str, position: int) -> str:
        selected = normalize_code(getattr(self, "selected_code", ""))
        if selected and code == selected:
            return "s1"
        return "top20" if position < 20 else "top100"

    def interval(self, metric: str, lane: str) -> float:
        policy = phase_policy(self)
        intervals = policy.get("intervals")
        intervals = intervals if isinstance(intervals, dict) else {}
        metric_intervals = intervals.get(metric)
        metric_intervals = metric_intervals if isinstance(metric_intervals, dict) else {}
        try:
            return max(0.0, float(metric_intervals.get(lane) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def metric_needs_backfill(self, code: str, metric: str, expected_date: str) -> bool:
        with self.state.lock:
            values = {
                **dict(self.state.daily_values_by_code.get(code) or {}),
                **dict(self.state.quotes.get(code) or {}),
            }
        return not _metric_complete(values, metric, expected_date)

    def next_task(self):
        now_mono = time.monotonic()
        phase = session_phase(self)
        policy = phase_policy(self)
        expected_date = metric_target_trading_date()
        missing_only = bool(policy.get("missing_only"))
        candidates: list[tuple[float, int, int, int, str, str, str, float]] = []
        codes = top_codes(self)

        for position, code in enumerate(codes):
            lane = lane_for(self, code, position)
            for metric in METRICS:
                if not self._metric_enabled(metric):
                    continue
                metric_interval = interval(self, metric, lane)
                if metric_interval <= 0:
                    continue
                if missing_only and not metric_needs_backfill(self, code, metric, expected_date):
                    continue
                key = (metric, code)
                if now_mono < self.error_backoff_until.get(key, 0.0):
                    continue
                last = self.last_requested.get(key)
                age = float("inf") if last is None else now_mono - last
                if age < metric_interval:
                    continue
                overdue = 1000.0 if last is None else age / max(metric_interval, 1.0)
                score = overdue * 1000.0
                candidates.append(
                    (
                        score,
                        LANE_PRIORITY[lane],
                        METRIC_PRIORITY[metric],
                        -position,
                        metric,
                        code,
                        lane,
                        metric_interval,
                    )
                )

        with self.state.lock:
            self.state.status["metric_session_phase"] = phase
            self.state.status["metric_session_expected_date"] = expected_date or None
            self.state.status["metric_session_missing_only"] = missing_only
            self.state.status["metric_session_ready_task_count"] = len(candidates)
        if not candidates:
            return None
        _score, _lane_priority, _metric_priority, _position, metric, code, lane, metric_interval = max(
            candidates
        )
        return metric, code, lane, metric_interval

    def query_code(self, code: str) -> str:
        phase = session_phase(self)
        mapping = self.config.get("query_suffix_by_session")
        suffix = mapping.get(phase) if isinstance(mapping, dict) else None
        if suffix in (None, ""):
            suffix = self.config.get("query_suffix") or "_AL"
        suffix = str(suffix).strip()
        query = f"{code}{suffix}" if suffix else code
        with self.state.lock:
            self.state.status["rest_live_metrics_market_phase"] = phase
            self.state.status["rest_live_metrics_query_suffix"] = suffix
            self.state.status["rest_live_metrics_query_code"] = query
        return query

    def collector_healthy(self) -> bool:
        phase = session_phase(self)
        policy = phase_policy(self)
        status = self._collector_status()
        ready = (
            status.get("running") is True
            and str(status.get("login_state") or "") == "connected"
            and status.get("realreg_succeeded") is True
            and int(status.get("realreg_code_count") or 0) > 0
            and not status.get("last_error")
        )
        now_mono = time.monotonic()
        if ready and bool(policy.get("require_trade_fresh")):
            stall_sec = max(10.0, float(self.config.get("price_stall_guard_sec") or 30))
            ready = now_mono - self.last_trade_change_mono <= stall_sec
        if not ready:
            self.health_stable_since = None
            return False
        if self.health_stable_since is None:
            self.health_stable_since = now_mono
            return False
        stable_sec = max(0.0, float(self.config.get("health_stable_sec") or 10))
        return now_mono - self.health_stable_since >= stable_sec

    def fetch(self, metric: str, code: str, metric_interval: float) -> dict[str, Any]:
        config = self._metric_config(metric)
        api_id = str(config.get("api_id") or metric)
        query = query_code(self, code)
        body: dict[str, Any] = {"stk_cd": query}
        if metric == "large_trade":
            body["tdy_pred"] = "1"
        phase = session_phase(self)
        target_date = metric_target_trading_date()
        return self.coordinator.execute(
            provider="kiwoom_rest",
            tr_code=f"{api_id}_{metric}",
            params=body,
            trading_date=target_date or "unknown",
            market_session=phase,
            ttl_sec=max(1.0, metric_interval * 0.8),
            wait_timeout_sec=20.0,
            lease_timeout_sec=30.0,
            stale_if_error=False,
            fetcher=lambda: self._physical_fetch(metric, code),
        )

    def run(self) -> None:
        loop_sleep = max(0.1, float(self.config.get("loop_sleep_sec") or 1.0))
        if not self.enabled or self.stage <= 0:
            self._status(rest_live_metrics_status="disabled")
            return

        last_phase_token: tuple[str, str] | None = None
        self._status(rest_live_metrics_status="waiting_market_session")
        while not self.stop_event.is_set():
            phase = session_phase(self)
            policy = phase_policy(self)
            expected_date = metric_target_trading_date()
            phase_token = (phase, expected_date)
            if phase_token != last_phase_token:
                self.last_requested.clear()
                self.error_backoff_until.clear()
                if last_phase_token is None or last_phase_token[1] != expected_date:
                    self.large_seen.clear()
                last_phase_token = phase_token
                self.health_stable_since = None
                try:
                    self.state.ensure_metric_session_state_date(force=True)
                except Exception as error:
                    self._status(
                        rest_live_metrics_status="state_rollover_error",
                        rest_live_metrics_last_error=f"{type(error).__name__}: {error}",
                    )
                with self.state.lock:
                    self.state.status["metric_session_phase_changed_at"] = now_text()
                    self.state.status["metric_session_phase_token"] = list(phase_token)

            if not bool(policy.get("active")) or int(policy.get("scope") or 0) <= 0:
                self._status(
                    rest_live_metrics_status="session_queries_paused",
                    rest_live_metrics_market_phase=phase,
                )
                self.stop_event.wait(2.0)
                continue
            if not collector_healthy(self):
                self.skip_health_count += 1
                self._status(
                    rest_live_metrics_status="waiting_price_health",
                    rest_live_metrics_market_phase=phase,
                )
                self.stop_event.wait(1.0)
                continue

            task = next_task(self)
            if task is None:
                self._status(
                    rest_live_metrics_status="session_complete_or_idle",
                    rest_live_metrics_market_phase=phase,
                )
                self.stop_event.wait(loop_sleep)
                continue

            metric, code, lane, metric_interval = task
            now_mono = time.monotonic()
            min_gap = max(1.0, float(policy.get("min_request_gap_sec") or 2.0))
            if now_mono - self.last_request_mono < min_gap or self._rest_busy():
                self.skip_busy_count += 1
                self._status(
                    rest_live_metrics_status="waiting_rest_budget",
                    rest_live_metrics_market_phase=phase,
                    metric_session_min_request_gap_sec=min_gap,
                )
                self.stop_event.wait(loop_sleep)
                continue

            key = (metric, code)
            self.last_requested[key] = now_mono
            self.last_request_mono = now_mono
            self.request_count += 1
            self._status(
                rest_live_metrics_status="requesting",
                rest_live_metrics_market_phase=phase,
                rest_live_metrics_last_metric=metric,
                rest_live_metrics_last_code=code,
                rest_live_metrics_last_lane=lane,
                rest_live_metrics_last_requested_at=now_text(),
                metric_session_min_request_gap_sec=min_gap,
            )
            try:
                payload = fetch(self, metric, code, metric_interval)
                applied = self._apply(code, metric, payload)
                self.success_count += 1
                self._status(
                    rest_live_metrics_status="ok" if applied else "no_data",
                    rest_live_metrics_last_completed_at=now_text(),
                    rest_live_metrics_last_error=None,
                )
            except Exception as error:
                self.error_count += 1
                backoff = max(5.0, float(self.config.get("error_backoff_sec") or 30))
                self.error_backoff_until[key] = time.monotonic() + backoff
                self._status(
                    rest_live_metrics_status="error_backoff",
                    rest_live_metrics_last_error=f"{type(error).__name__}: {error}",
                    rest_live_metrics_last_error_at=now_text(),
                )
            self.stop_event.wait(loop_sleep)

    updater_class._session_phase = session_phase
    updater_class._in_regular_session = active_session
    updater_class._top_codes = top_codes
    updater_class._interval = interval
    updater_class._next_task = next_task
    updater_class._query_code = query_code
    updater_class._price_healthy = collector_healthy
    updater_class._fetch = fetch
    updater_class.run = run
    updater_class._stockboard_market_session_manager_installed = True
