from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from realtime_v2.common import normalize_code, now_text
from realtime_v2.momentum_badge_engine import (
    MomentumBadgeEngine,
    load_momentum_badge_config,
)
from realtime_v2 import worker_momentum_1m_patch as momentum_1m

PATCH_VERSION = "momentum_badge_policy_v1"
PERSIST_KEYS = (
    "momentum_badge_state",
    "momentum_badge_trading_date",
    "momentum_badge_config_schema",
)


def _source_for_code(state, code: str) -> dict[str, Any]:
    return {
        **dict(state.daily_values_by_code.get(code) or {}),
        **dict(state.quotes.get(code) or {}),
    }


def _legacy_signals(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "open": {
            "label": source.get("momentum_open_signal"),
            "minute": source.get("momentum_open_signal_minute"),
        },
        "vwap": {
            "label": source.get("momentum_vwap_signal"),
            "minute": source.get("momentum_vwap_signal_minute"),
        },
    }


def _metadata_by_code(state) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    codes = set(state.name_by_code) | set(state.daily_values_by_code) | set(state.quotes)
    for code in codes:
        quote = state.quotes.get(code) or {}
        result[code] = {
            "stock_name": quote.get("stock_name") or state.name_by_code.get(code) or code,
            "rank": quote.get("rank") or state.seed_rank_by_code.get(code),
        }
    return result


def install(base) -> None:
    """Install configurable momentum badges without adding a data source or thread."""

    momentum_1m.install(base)
    state_class = getattr(base, "State", None)
    handler_class = getattr(base, "WebHandler", None)
    if state_class is None or handler_class is None or getattr(
        state_class,
        "_stockboard_momentum_badge_policy_installed",
        False,
    ):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *PERSIST_KEYS))
    )
    original_init = state_class.__init__
    original_stage = state_class.stage_approved_trade_events
    original_rows = state_class.rows
    original_reset = getattr(state_class, "reset_approved_minute_pipeline_for_date", None)
    original_rebuild = getattr(state_class, "request_background_rebuild", None)
    original_do_get = handler_class.do_GET

    def persist_code(self, code: str, trading_date: str) -> None:
        payload = self._momentum_badge_engine.serialize_code(code, trading_date)
        values = {
            "momentum_badge_state": payload,
            "momentum_badge_trading_date": trading_date,
            "momentum_badge_config_schema": self._momentum_badge_config["schema_version"],
        }
        for target in (
            self._quote(code),
            self.daily_values_by_code.setdefault(code, {}),
        ):
            target.update(deepcopy(values))
        self._mark_daily_dirty()

    def refresh_alert_cache(self, current_minute: int | None, force: bool = False) -> None:
        engine = self._momentum_badge_engine
        if not force and self._momentum_badge_alert_cache_version == engine.version:
            return
        config = self._momentum_badge_config
        alerts = (
            engine.alert_rows(_metadata_by_code(self), current_minute)
            if config["top_alert"]["enabled"]
            else []
        )
        self._momentum_badge_alert_cache = {
            "version": engine.version,
            "count": len(alerts),
            "items": alerts,
            "page_size": config["top_alert"]["page_size"],
            "rotate_interval_ms": config["top_alert"]["rotate_interval_ms"],
            "alternate_interval_ms": config["display"]["alternate_interval_ms"],
            "updated_at": now_text(),
        }
        self._momentum_badge_alert_cache_version = engine.version
        self.status.update(
            {
                "momentum_alert_version": engine.version,
                "momentum_alert_count": len(alerts),
                "momentum_alert_updated_at": self._momentum_badge_alert_cache["updated_at"],
            }
        )

    def observe_source(self, code: str, trading_date: str) -> bool:
        source = _source_for_code(self, code)
        candle = source.get("momentum_last_completed_candle")
        if not isinstance(candle, dict):
            return False
        changed = self._momentum_badge_engine.observe_completed_candle(
            code,
            candle,
            day_open=momentum_1m._nested_open(source),
            trading_date=trading_date,
            legacy_signals=_legacy_signals(source),
        )
        if changed:
            persist_code(self, code, trading_date)
        return changed

    def sync_all_completed(self, trading_date: str, current_minute: int | None) -> bool:
        if current_minute is None or self._momentum_badge_last_full_scan_minute == current_minute:
            return False
        self._momentum_badge_last_full_scan_minute = current_minute
        changed = False
        codes = set(getattr(self, "_momentum_last_completed", {}) or {})
        codes.update(self.daily_values_by_code)
        for code in codes:
            normalized = normalize_code(code)
            if normalized:
                changed = observe_source(self, normalized, trading_date) or changed
        return changed

    def state_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        config = load_momentum_badge_config()
        engine = MomentumBadgeEngine(config)
        trading_date = momentum_1m._expected_date()
        for code, daily in self.daily_values_by_code.items():
            if not isinstance(daily, dict):
                continue
            engine.restore_code(code, daily.get("momentum_badge_state"), trading_date)
            candle = daily.get("momentum_last_completed_candle")
            if isinstance(candle, dict):
                engine.seed_last_candle(code, candle)
        self._momentum_badge_config = config
        self._momentum_badge_engine = engine
        self._momentum_badge_alert_cache: dict[str, Any] = {}
        self._momentum_badge_alert_cache_version = -1
        self._momentum_badge_last_full_scan_minute: int | None = None
        refresh_alert_cache(self, momentum_1m._system_minute_key(), force=True)
        with self.lock:
            self.status.update(
                {
                    "momentum_badge_policy_installed": True,
                    "momentum_badge_policy_version": PATCH_VERSION,
                    "momentum_badge_config_path": config["path"],
                    "momentum_badge_config_schema": config["schema_version"],
                    "momentum_badge_rule_count": len(config["rules"]),
                    "momentum_badge_extra_qax_fids": 0,
                    "momentum_badge_extra_rest_requests": 0,
                    "momentum_badge_extra_websockets": 0,
                    "momentum_badge_extra_threads": 0,
                }
            )

    def rebuild_guard(self, *args, **kwargs):
        reason = kwargs.get("reason")
        if reason is None and args:
            reason = args[0]
        if reason == "momentum_1m_signal":
            with self.lock:
                self.status["momentum_badge_suppressed_candle_rebuild_count"] = int(
                    self.status.get("momentum_badge_suppressed_candle_rebuild_count") or 0
                ) + 1
            return None
        if callable(original_rebuild):
            return original_rebuild(self, *args, **kwargs)
        return None

    def stage_events(self, events):
        result = original_stage(self, events)
        trading_date = momentum_1m._expected_date()
        changed = False
        codes = {
            normalize_code(event.get("stock_code"))
            for event in (events if isinstance(events, list) else [])
            if isinstance(event, dict)
        }
        with self.lock:
            for code in codes:
                if code:
                    changed = observe_source(self, code, trading_date) or changed
            if changed:
                refresh_alert_cache(self, momentum_1m._system_minute_key(), force=True)
        if changed:
            self.request_background_rebuild(reason="momentum_badge_signal_change", force=False)
        return result

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        current_minute = momentum_1m._system_minute_key()
        trading_date = momentum_1m._expected_date()
        changed = False
        with self.lock:
            changed = sync_all_completed(self, trading_date, current_minute) or changed
            expired = self._momentum_badge_engine.expire(current_minute)
            for code in expired:
                persist_code(self, code, trading_date)
            changed = bool(expired) or changed
            if changed:
                refresh_alert_cache(self, current_minute, force=True)
            else:
                refresh_alert_cache(self, current_minute)
            config = self._momentum_badge_config
            for row in result:
                if not isinstance(row, dict):
                    continue
                code = normalize_code(row.get("stock_code"))
                badges = self._momentum_badge_engine.badges(code, current_minute)
                row["momentum_badges"] = badges
                row["momentum_badge_count"] = len(badges)
                row["momentum_badge_alternate_ms"] = config["display"][
                    "alternate_interval_ms"
                ]
                row["momentum_status"] = "active" if badges else "none"
                row.pop("momentum_details", None)
                row.pop("momentum_closed_hold", None)
            self.status["momentum_badge_active_code_count"] = len(
                self._momentum_badge_engine.states
            )
            self.status["momentum_badge_last_rows_at"] = now_text()
        return result

    def momentum_alert_payload(self) -> dict[str, Any]:
        current_minute = momentum_1m._system_minute_key()
        trading_date = momentum_1m._expected_date()
        with self.lock:
            expired = self._momentum_badge_engine.expire(current_minute)
            for code in expired:
                persist_code(self, code, trading_date)
            refresh_alert_cache(self, current_minute, force=bool(expired))
            return deepcopy(self._momentum_badge_alert_cache)

    def reset_for_date(self, target: str, phase: str):
        result = original_reset(self, target, phase) if callable(original_reset) else None
        with self.lock:
            self._momentum_badge_engine = MomentumBadgeEngine(self._momentum_badge_config)
            self._momentum_badge_alert_cache = {}
            self._momentum_badge_alert_cache_version = -1
            self._momentum_badge_last_full_scan_minute = None
            for collection in (self.quotes, self.daily_values_by_code):
                for values in collection.values():
                    if not isinstance(values, dict):
                        continue
                    for key in PERSIST_KEYS:
                        values.pop(key, None)
            refresh_alert_cache(self, momentum_1m._system_minute_key(), force=True)
            self.status["momentum_badge_rollover_at"] = now_text()
            self.status["momentum_badge_rollover_date"] = target
        return result

    def do_get(self) -> None:
        if urlparse(self.path).path == "/api/v2/momentum_alerts":
            self._json(self.server.state.momentum_alert_payload())
            return
        return original_do_get(self)

    state_class.__init__ = state_init
    state_class.request_background_rebuild = rebuild_guard
    state_class.stage_approved_trade_events = stage_events
    state_class.rows = rows
    state_class.momentum_alert_payload = momentum_alert_payload
    if callable(original_reset):
        state_class.reset_approved_minute_pipeline_for_date = reset_for_date
    handler_class.do_GET = do_get
    state_class._stockboard_momentum_badge_policy_installed = True
