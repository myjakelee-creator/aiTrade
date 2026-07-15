from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number, trading_date_text
from realtime_v2.tr_singleflight import get_shared_tr_coordinator

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "stockboard_live_metrics_rest.json"
SELECTED_PATH = ROOT / "data" / "runtime" / "stockboard_v2" / "selected_code.json"

PERSIST_KEYS = (
    "bid_ask_ratio",
    "bid_pct",
    "ask_pct",
    "bid_volume",
    "ask_volume",
    "best_ask_price",
    "best_bid_price",
    "orderbook_received_at",
    "orderbook_source",
    "orderbook_status",
    "orderbook_display_basis",
    "last_valid_bid_ask_ratio",
    "last_valid_bid_pct",
    "last_valid_ask_pct",
    "last_valid_bid_volume",
    "last_valid_ask_volume",
    "last_valid_orderbook_at",
    "execution_strength",
    "execution_strength_source",
    "execution_strength_status",
    "execution_strength_updated_at",
    "last_valid_execution_strength",
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "strength_source",
    "strength_status",
    "strength_snapshot_at",
    "last_valid_strength_5m",
    "last_valid_strength_at",
    "large_trade_buy_count",
    "large_trade_sell_count",
    "large_trade_net_count",
    "large_trade_buy_sum_eok",
    "large_trade_sell_sum_eok",
    "large_trade_net_sum_eok",
    "large_trade_source",
    "large_trade_status",
    "large_trade_threshold_krw",
    "large_trade_updated_at",
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_config() -> dict[str, Any]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "rollout_stage": 0, "metrics": {}}
    if not isinstance(payload, dict):
        return {"enabled": False, "rollout_stage": 0, "metrics": {}}
    return payload


def _number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    return float(number)


def _positive_abs(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    number = abs(number)
    return number if number > 0 else 0.0


def _digits(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _clock_minutes(text: Any, fallback: str) -> int:
    value = str(text or fallback).strip()
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def parse_bidask_payload(payload: dict[str, Any], *, updated_at: str | None = None) -> dict[str, Any]:
    ask_volume = _positive_abs(payload.get("tot_sel_req"))
    bid_volume = _positive_abs(payload.get("tot_buy_req"))
    if ask_volume is None or bid_volume is None or ask_volume + bid_volume <= 0:
        return {}

    if ask_volume > 0:
        ratio = bid_volume / ask_volume
    elif bid_volume > 0:
        ratio = 20.0
    else:
        return {}
    ratio = round(max(0.01, min(20.0, ratio)), 4)
    total = ask_volume + bid_volume
    bid_pct = round(bid_volume / total * 100, 4)
    ask_pct = round(100.0 - bid_pct, 4)
    timestamp = updated_at or now_text()

    result: dict[str, Any] = {
        "bid_ask_ratio": ratio,
        "bid_pct": bid_pct,
        "ask_pct": ask_pct,
        "bid_volume": int(bid_volume),
        "ask_volume": int(ask_volume),
        "orderbook_received_at": timestamp,
        "orderbook_source": "ka10004_rest_lowload",
        "orderbook_status": "ok",
        "orderbook_display_basis": "ka10004_rest_live",
        "last_valid_bid_ask_ratio": ratio,
        "last_valid_bid_pct": bid_pct,
        "last_valid_ask_pct": ask_pct,
        "last_valid_bid_volume": int(bid_volume),
        "last_valid_ask_volume": int(ask_volume),
        "last_valid_orderbook_at": timestamp,
    }
    best_ask = _positive_abs(payload.get("sel_fpr_bid"))
    best_bid = _positive_abs(payload.get("buy_fpr_bid"))
    if best_ask is not None:
        result["best_ask_price"] = int(best_ask)
    if best_bid is not None:
        result["best_bid_price"] = int(best_bid)
    return result


def _latest_strength_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = payload.get("cntr_str_tm")
    if not isinstance(rows, list):
        return None
    candidates = [row for row in rows if isinstance(row, dict)]
    if not candidates:
        return None
    candidates.sort(key=lambda row: _digits(row.get("cntr_tm")), reverse=True)
    return candidates[0]


def parse_strength_payload(
    payload: dict[str, Any],
    *,
    apply_five_minute: bool,
    updated_at: str | None = None,
) -> dict[str, Any]:
    row = _latest_strength_row(payload)
    if row is None:
        return {}
    instant = _number(row.get("cntr_str"))
    if instant is None or instant <= 0:
        return {}
    timestamp = updated_at or now_text()
    result: dict[str, Any] = {
        "execution_strength": round(instant, 4),
        "execution_strength_source": "ka10046_rest_lowload",
        "execution_strength_status": "ok",
        "execution_strength_updated_at": timestamp,
        "last_valid_execution_strength": round(instant, 4),
        "last_valid_strength_at": timestamp,
    }
    if apply_five_minute:
        five = _number(row.get("cntr_str_5min"))
        if five is not None and five > 0:
            result.update(
                {
                    "strength_5m": round(five, 4),
                    "strength_source": "ka10046_rest_lowload",
                    "strength_status": "ok",
                    "strength_snapshot_at": timestamp,
                    "last_valid_strength_5m": round(five, 4),
                }
            )
        for source_key, target_key in (
            ("cntr_str_20min", "strength_20m"),
            ("cntr_str_60min", "strength_60m"),
        ):
            value = _number(row.get(source_key))
            if value is not None and value > 0:
                result[target_key] = round(value, 4)
    return result


def parse_large_trade_page(
    payload: dict[str, Any],
    *,
    threshold_krw: int,
) -> list[dict[str, Any]]:
    rows = payload.get("tdy_pred_cntr_qty")
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _positive_abs(row.get("cntr_pric"))
        qty = _number(row.get("cntr_qty"))
        if price is None or qty in (None, 0):
            continue
        amount_krw = int(price * abs(qty))
        key = "|".join(
            (
                str(row.get("cntr_tm") or ""),
                str(row.get("cntr_pric") or ""),
                str(row.get("cntr_qty") or ""),
                str(row.get("acc_trde_qty") or ""),
            )
        )
        result.append(
            {
                "key": key,
                "qty": qty,
                "price": price,
                "amount_krw": amount_krw,
                "is_large": amount_krw >= int(threshold_krw),
            }
        )
    return result


class RestLiveMetricUpdater(threading.Thread):
    def __init__(self, state, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="stockboard-rest-live-metrics", daemon=True)
        self.state = state
        self.config = dict(config or _read_config())
        default_enabled = bool(self.config.get("enabled"))
        self.enabled = _env_bool("STOCKBOARD_LIVE_METRICS_REST_ENABLED", default_enabled)
        try:
            configured_stage = int(self.config.get("rollout_stage") or 0)
        except (TypeError, ValueError):
            configured_stage = 0
        try:
            self.stage = int(os.getenv("STOCKBOARD_LIVE_METRIC_STAGE", configured_stage))
        except (TypeError, ValueError):
            self.stage = configured_stage
        self.stage = max(0, min(4, self.stage))
        self.stop_event = threading.Event()
        self.coordinator = get_shared_tr_coordinator()
        self.access_token: str | None = None
        self.last_request_mono = 0.0
        self.last_requested: dict[tuple[str, str], float] = {}
        self.error_backoff_until: dict[tuple[str, str], float] = {}
        self.large_seen: dict[str, set[str]] = {}
        self.selected_code = ""
        self.selected_last_read = 0.0
        self.health_stable_since: float | None = None
        self.last_trade_count = -1
        self.last_trade_change_mono = time.monotonic()
        self.request_count = 0
        self.success_count = 0
        self.error_count = 0
        self.skip_busy_count = 0
        self.skip_health_count = 0
        self._initialize_status()

    def stop(self) -> None:
        self.stop_event.set()

    def _initialize_status(self) -> None:
        contract = self.config.get("performance_contract") or {}
        with self.state.lock:
            self.state.status.update(
                {
                    "rest_live_metrics_enabled": self.enabled,
                    "rest_live_metrics_stage": self.stage,
                    "rest_live_metrics_mode": self.config.get("mode") or "kiwoom_rest_lowload_v1",
                    "rest_live_metrics_price_collector_changes": contract.get("price_collector_changes", 0),
                    "rest_live_metrics_new_qax_processes": contract.get("new_qax_processes", 0),
                    "rest_live_metrics_new_realtime_fids": contract.get("new_realtime_fids", 0),
                    "rest_live_metrics_status": "starting" if self.enabled and self.stage > 0 else "disabled",
                }
            )

    def _status(self, **values: Any) -> None:
        with self.state.lock:
            self.state.status.update(values)
            self.state.status["rest_live_metrics_request_count"] = self.request_count
            self.state.status["rest_live_metrics_success_count"] = self.success_count
            self.state.status["rest_live_metrics_error_count"] = self.error_count
            self.state.status["rest_live_metrics_skip_busy_count"] = self.skip_busy_count
            self.state.status["rest_live_metrics_skip_health_count"] = self.skip_health_count
            self.state.status["rest_live_metrics_tr_singleflight"] = self.coordinator.status()

    def _collector_status(self) -> dict[str, Any]:
        with self.state.lock:
            event = deepcopy(self.state.status.get("collector_status") or {})
            trade_count = int(self.state.status.get("trade_count") or 0)
        status = event.get("status") if isinstance(event.get("status"), dict) else event
        if trade_count != self.last_trade_count:
            self.last_trade_count = trade_count
            self.last_trade_change_mono = time.monotonic()
        return status if isinstance(status, dict) else {}

    def _price_healthy(self) -> bool:
        status = self._collector_status()
        ready = (
            status.get("running") is True
            and str(status.get("login_state") or "") == "connected"
            and status.get("realreg_succeeded") is True
            and int(status.get("realreg_code_count") or 0) > 0
            and not status.get("last_error")
        )
        now_mono = time.monotonic()
        if ready and self._in_regular_session():
            stall_sec = float(self.config.get("price_stall_guard_sec") or 30)
            ready = now_mono - self.last_trade_change_mono <= max(10.0, stall_sec)
        if not ready:
            self.health_stable_since = None
            return False
        if self.health_stable_since is None:
            self.health_stable_since = now_mono
            return False
        stable_sec = float(self.config.get("health_stable_sec") or 10)
        return now_mono - self.health_stable_since >= max(0.0, stable_sec)

    def _minute_now(self) -> int:
        now = datetime.now()
        return now.hour * 60 + now.minute

    def _in_regular_session(self) -> bool:
        session = self.config.get("regular_session") or {}
        minute = self._minute_now()
        start = _clock_minutes(session.get("start"), "09:00")
        end = _clock_minutes(session.get("end"), "15:30")
        return start <= minute < end

    def _opening(self) -> bool:
        window = self.config.get("opening_window") or {}
        minute = self._minute_now()
        start = _clock_minutes(window.get("start"), "09:00")
        end = _clock_minutes(window.get("end"), "09:10")
        return start <= minute < end

    def _refresh_selected(self) -> None:
        now_mono = time.monotonic()
        interval = float(self.config.get("selected_refresh_sec") or 5)
        if now_mono - self.selected_last_read < max(1.0, interval):
            return
        self.selected_last_read = now_mono
        try:
            payload = json.loads(SELECTED_PATH.read_text(encoding="utf-8-sig"))
            self.selected_code = normalize_code(payload.get("stock_code")) if isinstance(payload, dict) else ""
        except (OSError, json.JSONDecodeError):
            self.selected_code = ""

    def _top_codes(self) -> list[str]:
        self._refresh_selected()
        limit = max(1, min(20, int(self.config.get("max_top_codes") or 20)))
        with self.state.lock:
            rows = [
                (normalize_code(code), _number(quote.get("trade_value_eok")) or 0.0)
                for code, quote in self.state.quotes.items()
                if isinstance(quote, dict) and normalize_code(code)
            ]
        rows.sort(key=lambda item: (-item[1], item[0]))
        codes = [code for code, _value in rows[:limit]]
        if self.selected_code and self.selected_code not in codes:
            codes.insert(0, self.selected_code)
        return codes

    def _metric_config(self, metric: str) -> dict[str, Any]:
        metrics = self.config.get("metrics") or {}
        value = metrics.get(metric)
        return dict(value) if isinstance(value, dict) else {}

    def _metric_enabled(self, metric: str) -> bool:
        config = self._metric_config(metric)
        try:
            required_stage = int(config.get("stage") or 99)
        except (TypeError, ValueError):
            return False
        return self.enabled and self.stage >= required_stage

    def _interval(self, metric: str, lane: str) -> float:
        config = self._metric_config(metric)
        key = "opening_interval_sec" if self._opening() else "regular_interval_sec"
        values = config.get(key) if isinstance(config.get(key), dict) else {}
        try:
            return max(0.0, float(values.get(lane) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _next_task(self) -> tuple[str, str, str, float] | None:
        now_mono = time.monotonic()
        candidates: list[tuple[float, int, str, str, str, float]] = []
        codes = self._top_codes()
        for position, code in enumerate(codes):
            lane = "s1" if code == self.selected_code and self.selected_code else "top20"
            lane_priority = 2 if lane == "s1" else 1
            for metric in ("bidask", "strength", "large_trade"):
                if not self._metric_enabled(metric):
                    continue
                interval = self._interval(metric, lane)
                if interval <= 0:
                    continue
                key = (metric, code)
                if now_mono < self.error_backoff_until.get(key, 0.0):
                    continue
                last = self.last_requested.get(key)
                age = float("inf") if last is None else now_mono - last
                if age < interval:
                    continue
                overdue = 9999.0 if last is None else age / max(interval, 1.0)
                metric_priority = {"bidask": 3, "strength": 2, "large_trade": 1}[metric]
                score = lane_priority * 10000 + metric_priority * 100 + overdue
                candidates.append((score, -position, metric, code, lane, interval))
        if not candidates:
            return None
        _score, _position, metric, code, lane, interval = max(candidates)
        return metric, code, lane, interval

    def _rest_busy(self) -> bool:
        try:
            for lease in self.coordinator.root.glob("*.lease"):
                try:
                    if time.time() - lease.stat().st_mtime <= 120:
                        return True
                except OSError:
                    continue
        except OSError:
            return False
        return False

    def _query_code(self, code: str) -> str:
        suffix = str(self.config.get("query_suffix") or "").strip()
        return f"{code}{suffix}" if suffix else code

    def _token(self, *, refresh: bool = False) -> str:
        if refresh or not self.access_token:
            from kiwoom_data_provider import issue_access_token

            self.access_token = issue_access_token()
        return self.access_token

    def _physical_fetch(self, metric: str, code: str) -> dict[str, Any]:
        from kiwoom_data_provider import _post_json

        config = self._metric_config(metric)
        api_id = str(config.get("api_id") or "")
        path = str(config.get("path") or "")
        query_code = self._query_code(code)
        body: dict[str, Any] = {"stk_cd": query_code}
        if metric == "large_trade":
            body["tdy_pred"] = "1"

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                token = self._token(refresh=attempt > 0)
                payload = _post_json(
                    path,
                    body,
                    {
                        "Authorization": f"Bearer {token}",
                        "api-id": api_id,
                        "cont-yn": "N",
                        "next-key": "",
                    },
                )
                if not isinstance(payload, dict):
                    raise TypeError(f"{api_id} returned non-dict payload")
                if payload.get("return_code") not in (None, 0, "0"):
                    raise RuntimeError(f"{api_id} failed: {payload}")
                return payload
            except Exception as error:
                last_error = error
        assert last_error is not None
        raise last_error

    def _fetch(self, metric: str, code: str, interval: float) -> dict[str, Any]:
        config = self._metric_config(metric)
        api_id = str(config.get("api_id") or metric)
        query_code = self._query_code(code)
        body = {"stk_cd": query_code}
        if metric == "large_trade":
            body["tdy_pred"] = "1"
        return self.coordinator.execute(
            provider="kiwoom_rest",
            tr_code=f"{api_id}_{metric}",
            params=body,
            trading_date=trading_date_text(),
            market_session="regular_lowload_metrics",
            ttl_sec=max(1.0, interval * 0.8),
            wait_timeout_sec=20.0,
            lease_timeout_sec=30.0,
            stale_if_error=False,
            fetcher=lambda: self._physical_fetch(metric, code),
        )

    def _apply(self, code: str, metric: str, payload: dict[str, Any]) -> bool:
        timestamp = now_text()
        if metric == "bidask":
            values = parse_bidask_payload(payload, updated_at=timestamp)
            if not values:
                return False
            self.state.apply_rest_live_metric_values(code, values, metric)
            return True
        if metric == "strength":
            strength_config = self._metric_config("strength")
            apply_stage = int(strength_config.get("strength_5m_apply_stage") or 3)
            values = parse_strength_payload(
                payload,
                apply_five_minute=self.stage >= apply_stage,
                updated_at=timestamp,
            )
            if not values:
                return False
            self.state.apply_rest_live_metric_values(code, values, metric)
            return True
        if metric == "large_trade":
            config = self._metric_config(metric)
            threshold = int(config.get("threshold_krw") or 50_000_000)
            rows = parse_large_trade_page(payload, threshold_krw=threshold)
            keys = {str(row.get("key") or "") for row in rows if row.get("key")}
            if code not in self.large_seen:
                self.large_seen[code] = keys
                self._status(
                    rest_live_metrics_large_trade_baseline_code=code,
                    rest_live_metrics_large_trade_baseline_count=len(keys),
                    rest_live_metrics_large_trade_baseline_at=timestamp,
                )
                return True
            seen = self.large_seen[code]
            new_rows = [row for row in rows if row.get("key") not in seen]
            seen.update(keys)
            if len(seen) > 2000:
                self.large_seen[code] = set(keys)
            buy_count = sell_count = 0
            buy_sum = sell_sum = 0.0
            for row in new_rows:
                if not row.get("is_large"):
                    continue
                qty = _number(row.get("qty"))
                amount_eok = float(row.get("amount_krw") or 0) / 100_000_000
                if qty is not None and qty > 0:
                    buy_count += 1
                    buy_sum += amount_eok
                elif qty is not None and qty < 0:
                    sell_count += 1
                    sell_sum += amount_eok
            if buy_count or sell_count:
                self.state.apply_rest_large_trade_delta(
                    code,
                    buy_count=buy_count,
                    sell_count=sell_count,
                    buy_sum_eok=buy_sum,
                    sell_sum_eok=sell_sum,
                    threshold_krw=threshold,
                    updated_at=timestamp,
                )
            return True
        return False

    def run(self) -> None:
        loop_sleep = max(0.1, float(self.config.get("loop_sleep_sec") or 0.25))
        if not self.enabled or self.stage <= 0:
            self._status(rest_live_metrics_status="disabled")
            return
        self._status(rest_live_metrics_status="waiting_price_health")
        while not self.stop_event.is_set():
            if not self._in_regular_session():
                self._status(rest_live_metrics_status="outside_regular_session")
                self.stop_event.wait(2.0)
                continue
            if not self._price_healthy():
                self.skip_health_count += 1
                self._status(rest_live_metrics_status="waiting_price_health")
                self.stop_event.wait(1.0)
                continue
            task = self._next_task()
            if task is None:
                self._status(rest_live_metrics_status="idle")
                self.stop_event.wait(loop_sleep)
                continue
            metric, code, lane, interval = task
            now_mono = time.monotonic()
            min_gap = max(1.0, float(self.config.get("global_min_request_gap_sec") or 1.5))
            if now_mono - self.last_request_mono < min_gap or self._rest_busy():
                self.skip_busy_count += 1
                self._status(rest_live_metrics_status="waiting_rest_budget")
                self.stop_event.wait(loop_sleep)
                continue

            key = (metric, code)
            self.last_requested[key] = now_mono
            self.last_request_mono = now_mono
            self.request_count += 1
            self._status(
                rest_live_metrics_status="requesting",
                rest_live_metrics_last_metric=metric,
                rest_live_metrics_last_code=code,
                rest_live_metrics_last_lane=lane,
                rest_live_metrics_last_requested_at=now_text(),
            )
            try:
                payload = self._fetch(metric, code, interval)
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


def install(base) -> None:
    state_class = getattr(base, "State", None)
    updater_class = getattr(base, "ProgramNetUpdater", None)
    if state_class is None or updater_class is None:
        return
    if getattr(updater_class, "_stockboard_rest_live_metrics_installed", False):
        return

    base.DAILY_PERSIST_KEYS = tuple(dict.fromkeys((*base.DAILY_PERSIST_KEYS, *PERSIST_KEYS)))

    def apply_rest_live_metric_values(
        self,
        raw_code: str,
        values: dict[str, Any],
        metric: str,
    ) -> int:
        code = normalize_code(raw_code)
        if not code or not isinstance(values, dict):
            return 0
        with self.lock:
            quote = self._quote(code)
            entry = self.daily_values_by_code.setdefault(code, {})
            for key, value in values.items():
                if value in (None, ""):
                    continue
                quote[key] = deepcopy(value)
                if key in PERSIST_KEYS:
                    entry[key] = deepcopy(value)
            status_key = f"rest_live_{metric}_update_count"
            self.status[status_key] = int(self.status.get(status_key) or 0) + 1
            self.status[f"rest_live_{metric}_last_code"] = code
            self.status[f"rest_live_{metric}_last_at"] = now_text()
            self._mark_daily_dirty()
        rebuild = getattr(self, "request_background_rebuild", None)
        if callable(rebuild):
            rebuild(reason=f"rest_live_metric:{metric}", force=False)
        return 1

    def apply_rest_large_trade_delta(
        self,
        raw_code: str,
        *,
        buy_count: int,
        sell_count: int,
        buy_sum_eok: float,
        sell_sum_eok: float,
        threshold_krw: int,
        updated_at: str,
    ) -> int:
        code = normalize_code(raw_code)
        if not code:
            return 0
        with self.lock:
            quote = self._quote(code)
            quote["large_trade_buy_count"] = int(quote.get("large_trade_buy_count") or 0) + int(buy_count)
            quote["large_trade_sell_count"] = int(quote.get("large_trade_sell_count") or 0) + int(sell_count)
            quote["large_trade_buy_sum_eok"] = round(
                float(quote.get("large_trade_buy_sum_eok") or 0.0) + float(buy_sum_eok), 4
            )
            quote["large_trade_sell_sum_eok"] = round(
                float(quote.get("large_trade_sell_sum_eok") or 0.0) + float(sell_sum_eok), 4
            )
            quote["large_trade_net_count"] = int(quote["large_trade_buy_count"]) - int(
                quote["large_trade_sell_count"]
            )
            quote["large_trade_net_sum_eok"] = round(
                float(quote["large_trade_buy_sum_eok"])
                - float(quote["large_trade_sell_sum_eok"]),
                4,
            )
            quote["large_trade_source"] = "ka10055_rest_incremental"
            quote["large_trade_status"] = "partial_recent_page"
            quote["large_trade_threshold_krw"] = int(threshold_krw)
            quote["large_trade_updated_at"] = updated_at
            entry = self.daily_values_by_code.setdefault(code, {})
            for key in PERSIST_KEYS:
                if key.startswith("large_trade_") and quote.get(key) not in (None, ""):
                    entry[key] = deepcopy(quote.get(key))
            self.status["rest_live_large_trade_update_count"] = int(
                self.status.get("rest_live_large_trade_update_count") or 0
            ) + 1
            self.status["rest_live_large_trade_last_code"] = code
            self.status["rest_live_large_trade_last_at"] = updated_at
            self._mark_daily_dirty()
        rebuild = getattr(self, "request_background_rebuild", None)
        if callable(rebuild):
            rebuild(reason="rest_live_metric:large_trade", force=False)
        return 1

    state_class.apply_rest_live_metric_values = apply_rest_live_metric_values
    state_class.apply_rest_large_trade_delta = apply_rest_large_trade_delta

    original_updater_class = updater_class

    class ProgramNetUpdaterWithRestMetrics(original_updater_class):
        _stockboard_rest_live_metrics_installed = True

        def __init__(self, state, *args, **kwargs):
            super().__init__(state, *args, **kwargs)
            self.rest_live_metric_updater = RestLiveMetricUpdater(state)

        def start(self) -> None:
            super().start()
            self.rest_live_metric_updater.start()

        def stop(self) -> None:
            self.rest_live_metric_updater.stop()
            super().stop()

    base.ProgramNetUpdater = ProgramNetUpdaterWithRestMetrics
