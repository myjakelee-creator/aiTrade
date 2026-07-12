from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote as urlquote

from realtime_v2.market_session import (
    last_completed_trading_date,
    market_session_now,
    next_premarket_datetime,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "runtime" / "stockboard_v2"
SAMPLER_FILE = RUNTIME / "close_flow_sampler_last.json"
AFTER_CLOSE_PHASES = {"closed", "before_market", "weekend", "holiday"}
RETRY_SEC = (300.0, 1800.0, 7200.0)
MINUTE_RQNAME, MINUTE_TRCODE, MINUTE_SCREEN = (
    "stockboard_opt10080_recovery",
    "opt10080",
    "9023",
)
MINUTE_FIELDS = ("현재가", "거래량", "체결시간", "시가", "고가", "저가")
QUALITY = {
    "LIVE_REALTIME": 1.0,
    "CLOSE_HISTORY_EXACT": 1.0,
    "TR_TRADE_VALUE": 0.95,
    "OPT10046": 0.95,
    "PROGRAM_BATCH": 0.95,
    "MINUTE_BAR_CLOSE": 0.90,
    "PERSISTED_LAST_VALID": 0.80,
    "MINUTE_CLOSE_X_VOLUME": 0.65,
    "PREVIOUS_DAY_DISPLAY": 0.40,
}


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        value = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def positive(value: Any) -> float | None:
    value = num(value)
    return value if value is not None and value > 0 else None


def date_text(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 14:
        try:
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        except ValueError:
            pass
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def scope_rank(value: Any) -> int:
    value = str(value or "").upper()
    if value in {"AL", "INTEGRATED", "AL_OR_DECLARED"}:
        return 3
    if value in {"NX", "NXT"}:
        return 2
    if value == "KRX":
        return 1
    return 0


def source_value(
    value: Any,
    source: str,
    status: str,
    basis_time: Any,
    trading_date: Any,
    market_scope: str,
    quality: float,
    is_estimated: bool,
    coverage: float = 1.0,
) -> dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "status": status,
        "basis_time": str(basis_time or ""),
        "trading_date": date_text(trading_date),
        "market_scope": market_scope,
        "quality": round(max(0.0, min(1.0, quality)), 4),
        "is_estimated": bool(is_estimated),
        "updated_at": now_text(),
        "coverage": round(max(0.0, min(1.0, coverage)), 4),
    }


def prefer_source_value(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    allow_rollover: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(candidate, dict) or candidate.get("value") in (None, ""):
        return deepcopy(existing) if isinstance(existing, dict) else None
    if not isinstance(existing, dict) or existing.get("value") in (None, ""):
        return deepcopy(candidate)
    old_date = date_text(existing.get("trading_date"))
    new_date = date_text(candidate.get("trading_date"))
    if old_date and new_date and old_date != new_date:
        return deepcopy(candidate if allow_rollover and new_date > old_date else existing)
    if scope_rank(existing.get("market_scope")) == 3 and scope_rank(candidate.get("market_scope")) == 1:
        return deepcopy(existing)
    old_q = float(existing.get("quality") or 0)
    new_q = float(candidate.get("quality") or 0)
    if not existing.get("is_estimated") and candidate.get("is_estimated") and new_q <= old_q:
        return deepcopy(existing)
    if new_q != old_q:
        return deepcopy(candidate if new_q > old_q else existing)
    old_scope = scope_rank(existing.get("market_scope"))
    new_scope = scope_rank(candidate.get("market_scope"))
    if new_scope != old_scope:
        return deepcopy(candidate if new_scope > old_scope else existing)
    old_time = parse_time(existing.get("basis_time") or existing.get("updated_at"))
    new_time = parse_time(candidate.get("basis_time") or candidate.get("updated_at"))
    return deepcopy(candidate if new_time and (not old_time or new_time >= old_time) else existing)


def minute_rows_to_recovery(
    rows: list[dict[str, Any]],
    trading_date: str,
    market_scope: str,
    snapshot_at: str,
) -> dict[str, Any]:
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    target = date_text(trading_date)
    for row in rows:
        close = positive(row.get("close") or row.get("현재가"))
        volume = positive(row.get("volume") or row.get("거래량"))
        stamp = str(row.get("time") or row.get("체결시간") or "")
        if close is None or volume is None:
            continue
        if target and date_text(stamp) and date_text(stamp) != target:
            continue
        minute = "".join(ch for ch in stamp if ch.isdigit())[:12] or str(len(parsed))
        if minute in seen:
            continue
        seen.add(minute)
        parsed.append(
            {
                "time": stamp,
                "close": abs(close),
                "volume": abs(volume),
                "open": abs(num(row.get("open") or row.get("시가")) or close),
                "high": abs(num(row.get("high") or row.get("고가")) or close),
                "low": abs(num(row.get("low") or row.get("저가")) or close),
                "amount_eok": abs(close) * abs(volume) / 100_000_000,
            }
        )
    parsed.sort(key=lambda row: "".join(ch for ch in row["time"] if ch.isdigit()), reverse=True)
    parsed = parsed[:5]
    if not parsed:
        return {
            "minute_recovery_status": "no_data",
            "minute_recovery_error": "no valid opt10080 minute rows",
            "minute_recovery_snapshot_at": snapshot_at,
            "minute_recovery_market_scope": market_scope,
            "recovery_values": {},
        }
    latest = parsed[0]
    oldest = parsed[-1]
    amount_1m = latest["amount_eok"]
    amount_5m = sum(row["amount_eok"] for row in parsed)
    coverage = min(1.0, len(parsed) / 5)
    ohlc = {
        "open": round(oldest["open"]),
        "high": round(max(row["high"] for row in parsed)),
        "low": round(min(row["low"] for row in parsed)),
        "close": round(latest["close"]),
    }
    basis = latest["time"] or snapshot_at
    values = {
        "price": source_value(
            round(latest["close"]),
            "MINUTE_BAR_CLOSE",
            "FALLBACK",
            basis,
            trading_date,
            market_scope,
            QUALITY["MINUTE_BAR_CLOSE"],
            False,
        ),
        "trade_value_1m_eok": source_value(
            round(amount_1m, 6),
            "MINUTE_CLOSE_X_VOLUME",
            "ESTIMATED",
            basis,
            trading_date,
            market_scope,
            QUALITY["MINUTE_CLOSE_X_VOLUME"],
            True,
        ),
        "trade_value_5m_eok": source_value(
            round(amount_5m, 6),
            "MINUTE_CLOSE_X_VOLUME",
            "ESTIMATED",
            basis,
            trading_date,
            market_scope,
            QUALITY["MINUTE_CLOSE_X_VOLUME"],
            True,
            coverage,
        ),
        "ohlc": source_value(
            ohlc,
            "MINUTE_BAR_OHLC",
            "FALLBACK",
            basis,
            trading_date,
            market_scope,
            QUALITY["MINUTE_CLOSE_X_VOLUME"],
            True,
            coverage,
        ),
    }
    return {
        "minute_recovery_status": "ok",
        "minute_recovery_error": None,
        "minute_recovery_snapshot_at": snapshot_at,
        "minute_recovery_market_scope": market_scope,
        "minute_recovery_row_count": len(parsed),
        "minute_close_price": round(latest["close"]),
        "minute_trade_value_1m_eok": round(amount_1m, 6),
        "minute_trade_value_5m_eok": round(amount_5m, 6),
        "minute_ohlc": ohlc,
        "minute_rows": parsed,
        "recovery_values": values,
    }


def provider_tr_idle(provider, gap: float = 0.0) -> tuple[bool, str]:
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return False, "provider_lock_missing"
    with lock:
        if not getattr(provider, "_running", False):
            return False, "provider_not_running"
        if str(getattr(provider, "_login_state", "")) != "connected":
            return False, f"login_state={getattr(provider, '_login_state', None)}"
        for name in (
            "_strength_probe_inflight",
            "_orderbook_probe_inflight",
            "_opt10055_probe_inflight",
            "_minute_recovery_inflight",
        ):
            if getattr(provider, name, None):
                return False, name
        for name in (
            "_strength_probe_pending",
            "_orderbook_probe_pending",
            "_opt10055_probe_pending",
            "_close_metrics_queue",
            "_minute_recovery_pending",
        ):
            if len(getattr(provider, name, ()) or ()):
                return False, name
        last = max(
            [
                float(getattr(provider, name, 0) or 0)
                for name in (
                    "_strength_probe_last_request_at",
                    "_orderbook_probe_last_request_at",
                    "_opt10055_probe_last_request_at",
                    "_close_metrics_last_request_at",
                    "_minute_recovery_last_request_at",
                )
            ]
            or [0]
        )
    remaining = gap - (time.monotonic() - last)
    if remaining > 0:
        return False, f"global_gap:{remaining:.3f}"
    return True, "ready"


def clock_at(now: datetime, text: Any, fallback: str) -> datetime:
    try:
        hour, minute = [int(part) for part in str(text or fallback).split(":")[:2]]
    except (TypeError, ValueError):
        hour, minute = [int(part) for part in fallback.split(":")]
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


class CloseWindowSamplerService(threading.Thread):
    def __init__(
        self,
        state,
        master_path: Path,
        persist_path: Path = SAMPLER_FILE,
        now_provider=None,
    ):
        super().__init__(name="stockboard-close-window-sampler", daemon=True)
        self.state = state
        self.master_path = Path(master_path)
        self.persist_path = Path(persist_path)
        self.now_provider = now_provider or datetime.now
        self.stop_event = threading.Event()
        self.samples: deque[tuple[datetime, dict[str, float], dict[str, str]]] = deque(maxlen=500)
        self.active_key = None
        self.active_end = None
        self.active_date = None
        self.last_sample_mono = 0.0
        self.sample_count = 0
        self.lock_busy_skip_count = 0
        self.persist_count = 0
        self.last_saved_at = None
        self.last_error = None
        self.theme_members: dict[str, tuple[tuple[str, float], ...]] = {}
        self.codes: set[str] = set()
        self._load_master()

    def _load_master(self):
        try:
            from stockboard_theme_engine import load_theme_master

            master = load_theme_master(self.master_path)
            self.theme_members = {
                key: tuple((code, float(weight)) for code, weight, _relation in members)
                for key, members in master.by_theme.items()
            }
            self.codes = {
                code
                for members in self.theme_members.values()
                for code, _weight in members
            }
        except Exception as error:
            self.last_error = f"master: {type(error).__name__}: {error}"

    def stop(self):
        self.stop_event.set()

    def _window(self, now: datetime):
        session = market_session_now(now)
        if not session.is_trading_day:
            return None
        windows = session.windows or {}
        for key, close_at in (
            ("regular", clock_at(now, windows.get("regular_close"), "15:30")),
            ("integrated", clock_at(now, windows.get("aftermarket_end"), "20:00")),
        ):
            if close_at - timedelta(seconds=310) <= now <= close_at + timedelta(seconds=10):
                return (
                    key,
                    close_at + timedelta(seconds=10),
                    str(session.trading_date or now.strftime("%Y%m%d")),
                )
        return None

    def _sample(self, now: datetime):
        lock = getattr(self.state, "lock", None)
        if lock is None:
            self.last_error = "state_lock_missing"
            return
        try:
            acquired = lock.acquire(blocking=False)
        except TypeError:
            acquired = lock.acquire(False)
        if not acquired:
            self.lock_busy_skip_count += 1
            return
        values: dict[str, float] = {}
        scopes: dict[str, str] = {}
        try:
            quotes = getattr(self.state, "quotes", {}) or {}
            for code in self.codes:
                row = quotes.get(code)
                value = num(row.get("trade_value_eok")) if isinstance(row, dict) else None
                if value is None or value < 0:
                    continue
                values[code] = value
                source = str(row.get("source_code") or row.get("realtime_source_code") or "")
                if source.endswith("_AL"):
                    scopes[code] = "AL"
                elif source.endswith("_NX"):
                    scopes[code] = "NX"
                else:
                    scopes[code] = "KRX"
        finally:
            lock.release()
        self.samples.append((now, values, scopes))
        self.sample_count += 1

    @staticmethod
    def _nearest(samples, target):
        before = [sample for sample in samples if sample[0] <= target]
        if before:
            return max(before, key=lambda item: item[0])
        if samples:
            return min(samples, key=lambda item: abs((item[0] - target).total_seconds()))
        return None

    def _finalize(self, key, end_at, trading_date):
        samples = list(self.samples)
        current = self._nearest(samples, end_at)
        one = self._nearest(samples, end_at - timedelta(seconds=60))
        five = self._nearest(samples, end_at - timedelta(seconds=300))
        if current is None:
            return
        current_at, current_values, scopes = current

        def delta(base, code):
            if base is None or code not in current_values or code not in base[1]:
                return None
            return max(0.0, current_values[code] - base[1][code])

        per_code = {}
        for code in self.codes:
            delta_1m = delta(one, code)
            delta_5m = delta(five, code)
            if delta_1m is not None or delta_5m is not None:
                per_code[code] = {
                    "trade_value_1m_eok": None if delta_1m is None else round(delta_1m, 6),
                    "trade_value_5m_eok": None if delta_5m is None else round(delta_5m, 6),
                    "market_scope": scopes.get(code, "UNKNOWN"),
                }
        themes = {}
        for theme_id, members in self.theme_members.items():
            total = sum(weight for _code, weight in members) or 1.0
            value_1m = 0.0
            value_5m = 0.0
            weight_1m = 0.0
            weight_5m = 0.0
            for code, weight in members:
                item = per_code.get(code) or {}
                if item.get("trade_value_1m_eok") is not None:
                    value_1m += item["trade_value_1m_eok"] * weight
                    weight_1m += weight
                if item.get("trade_value_5m_eok") is not None:
                    value_5m += item["trade_value_5m_eok"] * weight
                    weight_5m += weight
            themes[theme_id] = {
                "inflow_1m_eok": round(value_1m, 6) if weight_1m else None,
                "inflow_5m_eok": round(value_5m, 6) if weight_5m else None,
                "coverage_1m": round(min(1.0, weight_1m / total), 4),
                "coverage_5m": round(min(1.0, weight_5m / total), 4),
            }
        payload = {
            "schema_version": 1,
            "source": "CLOSE_SAMPLER_EXACT",
            "quality": 1.0,
            "is_estimated": False,
            "window": key,
            "trading_date": trading_date,
            "basis_time": current_at.isoformat(timespec="seconds"),
            "saved_at": now_text(),
            "sample_count": len(samples),
            "per_code": per_code,
            "themes": themes,
        }
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            paths = (
                self.persist_path,
                self.persist_path.with_name(f"close_flow_sampler_{trading_date}_{key}.json"),
            )
            for path in paths:
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, path)
            self.persist_count += 1
            self.last_saved_at = payload["saved_at"]
            self.last_error = None
        except OSError as error:
            self.last_error = f"persist: {error}"

    def status(self):
        return {
            "enabled": bool(self.codes),
            "alive": self.is_alive(),
            "active_window": self.active_key,
            "sample_count": self.sample_count,
            "buffer_size": len(self.samples),
            "lock_busy_skip_count": self.lock_busy_skip_count,
            "persist_count": self.persist_count,
            "last_saved_at": self.last_saved_at,
            "last_error": self.last_error,
            "persist_path": str(self.persist_path),
        }

    def _publish_status(self):
        lock = getattr(self.state, "lock", None)
        if lock is None:
            return
        try:
            acquired = lock.acquire(blocking=False)
        except TypeError:
            acquired = lock.acquire(False)
        if acquired:
            try:
                if isinstance(getattr(self.state, "status", None), dict):
                    self.state.status["close_flow_sampler"] = self.status()
            finally:
                lock.release()

    def run(self):
        while not self.stop_event.wait(0.25):
            now = self.now_provider()
            window = self._window(now)
            if window is None:
                if self.active_key and self.active_end and self.active_date and now > self.active_end:
                    self._finalize(self.active_key, self.active_end, self.active_date)
                    self.active_key = None
                    self.active_end = None
                    self.active_date = None
                    self.samples.clear()
                self._publish_status()
                continue
            key, end_at, trading_date = window
            if key != self.active_key:
                if self.active_key and self.active_end and self.active_date:
                    self._finalize(self.active_key, self.active_end, self.active_date)
                self.samples.clear()
                self.active_key = key
                self.active_end = end_at
                self.active_date = trading_date
                self.last_sample_mono = 0.0
            now_mono = time.monotonic()
            if now_mono - self.last_sample_mono >= 1.0:
                self.last_sample_mono = now_mono
                self._sample(now)
            self._publish_status()


def load_sampler(path: Path = SAMPLER_FILE) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def merge_sampler_theme(payload: dict[str, Any], sampler: dict[str, Any]) -> dict[str, Any]:
    values = sampler.get("themes") if isinstance(sampler, dict) else None
    if not isinstance(payload, dict) or not isinstance(values, dict):
        return payload
    payload = deepcopy(payload)
    themes = payload.get("themes") if isinstance(payload.get("themes"), list) else []
    max_1m = max(
        [num((values.get(str(item.get("theme_id"))) or {}).get("inflow_1m_eok")) or 0 for item in themes]
        or [0]
    )
    max_5m = max(
        [num((values.get(str(item.get("theme_id"))) or {}).get("inflow_5m_eok")) or 0 for item in themes]
        or [0]
    )
    for item in themes:
        recovered = values.get(str(item.get("theme_id") or ""))
        if not isinstance(recovered, dict):
            continue
        for key, maximum in (("1m", max_1m), ("5m", max_5m)):
            value = num(recovered.get(f"inflow_{key}_eok"))
            if value is None:
                continue
            item.update(
                {
                    f"inflow_{key}_value": round(value, 6),
                    f"inflow_{key}_text": f"{'+' if value > 0 else ''}{round(value):,}억",
                    f"inflow_{key}_tone": "plus" if value > 0 else "zero",
                    f"inflow_{key}_bar_pct": round(value / maximum * 100, 1) if maximum > 0 else 0,
                    f"inflow_{key}_source": "CLOSE_SAMPLER_EXACT",
                    f"inflow_{key}_quality": 1.0,
                    f"inflow_{key}_is_estimated": False,
                    f"inflow_{key}_coverage": recovered.get(f"coverage_{key}"),
                    f"inflow_{key}_basis_time": sampler.get("basis_time"),
                }
            )
    status = dict(payload.get("status") or {})
    status.update(
        {
            "close_flow_sampler_source": sampler.get("source"),
            "close_flow_sampler_basis_time": sampler.get("basis_time"),
            "close_flow_sampler_trading_date": sampler.get("trading_date"),
        }
    )
    payload["status"] = status
    return payload


def code_scope(value: Any) -> str:
    value = str(value or "")
    if value.endswith("_AL"):
        return "AL"
    if value.endswith("_NX"):
        return "NX"
    return "KRX" if value else "UNKNOWN"


def install_worker(base, large=None):
    if getattr(base, "_after_close_recovery_worker_installed", False):
        return
    State = base.State
    original_quote = State._quote
    original_trade = State._apply_trade
    original_orderbook = State._apply_orderbook
    original_close = State._apply_close_metrics
    original_program = State.apply_program_net_values

    def quote_method(self, code):
        row = original_quote(self, code)
        persisted = self.daily_values_by_code.get(code) or {}
        for key in (
            "minute_recovery_status",
            "minute_recovery_snapshot_at",
            "minute_recovery_market_scope",
            "minute_trade_value_1m_eok",
            "minute_trade_value_5m_eok",
            "minute_ohlc",
            "trade_value_1m_eok",
            "trade_value_5m_eok",
            "recovery_values",
            "source_metadata",
        ):
            if key in persisted and key not in row:
                row[key] = deepcopy(persisted[key])
        row.setdefault("source_metadata", {})
        return row

    def live_metadata(row, fields, basis, scope):
        metadata = row.setdefault("source_metadata", {})
        for field in fields:
            if row.get(field) not in (None, ""):
                metadata[field] = source_value(
                    row[field],
                    "LIVE_REALTIME",
                    "LIVE",
                    basis,
                    base.trading_date_text(),
                    scope,
                    1.0,
                    False,
                )

    def trade_method(self, event):
        original_trade(self, event)
        values = base.merged_event_values(event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        row = self.quotes.get(code) if code else None
        if isinstance(row, dict):
            live_metadata(
                row,
                ("price", "change_rate", "trade_value_eok", "execution_strength"),
                row.get("received_at") or now_text(),
                code_scope(row.get("source_code")),
            )

    def orderbook_method(self, event):
        original_orderbook(self, event)
        values = base.merged_event_values(event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        row = self.quotes.get(code) if code else None
        if isinstance(row, dict):
            live_metadata(
                row,
                ("bid_ask_ratio",),
                row.get("orderbook_received_at") or now_text(),
                code_scope(row.get("source_code")),
            )

    def existing_meta(row, field):
        metadata = row.setdefault("source_metadata", {})
        if isinstance(metadata.get(field), dict):
            return metadata[field]
        if row.get(field) in (None, ""):
            return None
        live = bool(
            row.get("received_at")
            and field in {"price", "change_rate", "trade_value_eok", "execution_strength", "bid_ask_ratio"}
        )
        return source_value(
            row[field],
            "LIVE_REALTIME" if live else "PERSISTED_LAST_VALID",
            "LIVE" if live else "HELD",
            row.get("received_at") or now_text(),
            base.trading_date_text(),
            code_scope(row.get("source_code")),
            1.0 if live else 0.8,
            False,
        )

    def close_method(self, event):
        values = base.merged_event_values(event)
        original_close(self, event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        row = self.quotes.get(code) if code else None
        if not isinstance(row, dict):
            return
        metadata = row.setdefault("source_metadata", {})
        recovery = values.get("recovery_values")
        if isinstance(recovery, dict):
            for field, candidate in recovery.items():
                preferred = prefer_source_value(existing_meta(row, field), candidate, allow_rollover=True)
                if preferred:
                    metadata[field] = preferred
                    if preferred == candidate:
                        row[field] = deepcopy(candidate.get("value"))
            entry = self.daily_values_by_code.setdefault(code, {})
            for key in (
                "minute_recovery_status",
                "minute_recovery_snapshot_at",
                "minute_recovery_market_scope",
                "minute_trade_value_1m_eok",
                "minute_trade_value_5m_eok",
                "minute_ohlc",
                "recovery_values",
            ):
                if key in values:
                    entry[key] = deepcopy(values[key])
            for key in ("trade_value_1m_eok", "trade_value_5m_eok"):
                if key in row:
                    entry[key] = row[key]
            entry["source_metadata"] = deepcopy(metadata)
            self._mark_daily_dirty()

    def program_method(self, values, source, status):
        updated = original_program(self, values, source, status)
        with self.lock:
            for raw_code, raw_value in (values or {}).items():
                code = base.normalize_code(raw_code)
                row = self.quotes.get(code)
                value = num(raw_value.get("program_net") if isinstance(raw_value, dict) else raw_value)
                if isinstance(row, dict) and value is not None:
                    row.setdefault("source_metadata", {})["program_net"] = source_value(
                        value,
                        "PROGRAM_BATCH",
                        str(status or "EXACT"),
                        now_text(),
                        base.trading_date_text(),
                        "AL_OR_DECLARED",
                        0.95,
                        False,
                    )
        return updated

    State._quote = quote_method
    State._apply_trade = trade_method
    State._apply_orderbook = orderbook_method
    State._apply_close_metrics = close_method
    State.apply_program_net_values = program_method

    original_server_init = base.WebServer.__init__
    original_server_close = base.WebServer.server_close

    def server_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        service = CloseWindowSamplerService(
            state,
            ROOT / "config" / "stockboard_theme_master.json",
        )
        self.close_window_sampler_service = service
        state.close_window_sampler_service = service
        service.start()

    def server_close(self):
        service = getattr(self, "close_window_sampler_service", None)
        if service:
            service.stop()
            service.join(timeout=2)
        return original_server_close(self)

    base.WebServer.__init__ = server_init
    base.WebServer.server_close = server_close

    try:
        import realtime_v2.theme_board_patch as theme_patch

        theme_class = theme_patch.ThemeCacheService
        if not getattr(theme_class, "_after_close_sampler_installed", False):
            original_close_payload = theme_class._close_payload
            original_theme_status = theme_class.status

            def close_payload(self, payload, details, session, hold_until):
                payload, details = original_close_payload(
                    self,
                    payload,
                    details,
                    session,
                    hold_until,
                )
                sampler = load_sampler()
                if sampler:
                    payload = merge_sampler_theme(payload, sampler)
                return payload, details

            def theme_status(self):
                result = original_theme_status(self)
                result["close_flow_sampler"] = deepcopy(
                    (getattr(self.state, "status", {}) or {}).get("close_flow_sampler")
                )
                return result

            theme_class._close_payload = close_payload
            theme_class.status = theme_status
            theme_class._after_close_sampler_installed = True
    except Exception:
        pass
    base._after_close_recovery_worker_installed = True


def get_comm(control, tr_code, rq_name, index, field):
    value = control.dynamicCall(
        "GetCommData(QString, QString, int, QString)",
        tr_code,
        rq_name,
        index,
        field,
    )
    return "" if value is None else str(value).strip()


def minute_result(provider, inflight, tr_code, rq_name, record_name, screen_no):
    control = provider._control
    try:
        repeat = int(
            control.dynamicCall("GetRepeatCnt(QString, QString)", tr_code, rq_name) or 0
        )
    except Exception:
        repeat = 0
    rows = []
    raw_sample = []
    for index in range(min(max(repeat, 0), 10)):
        raw = {
            field: get_comm(control, tr_code, rq_name, index, field)
            for field in MINUTE_FIELDS
        }
        rows.append(
            {
                "close": abs(num(raw["현재가"]) or 0),
                "volume": abs(num(raw["거래량"]) or 0),
                "time": raw["체결시간"],
                "open": abs(num(raw["시가"]) or 0),
                "high": abs(num(raw["고가"]) or 0),
                "low": abs(num(raw["저가"]) or 0),
            }
        )
        if len(raw_sample) < 5:
            raw_sample.append(raw)
    result = minute_rows_to_recovery(
        rows,
        str(inflight.get("trading_date") or ""),
        str(inflight.get("market_scope") or "AL"),
        now_text(),
    )
    result.update(
        {
            "stock_code": inflight.get("stock_code"),
            "trading_date": inflight.get("trading_date"),
            "minute_recovery_source_code": inflight.get("request_code"),
            "minute_recovery_requested_at": inflight.get("requested_at"),
            "minute_recovery_completed_at": now_text(),
            "minute_recovery_rqname": rq_name,
            "minute_recovery_trcode": tr_code,
            "minute_recovery_screen_no": screen_no,
            "minute_recovery_repeat_count": repeat,
            "minute_recovery_raw_sample": raw_sample,
            "minute_recovery_status_detail": (
                f"screen={screen_no}, tr={tr_code}, record={record_name}, repeat_count={repeat}"
            ),
        }
    )
    return result


def minute_error(provider, code, message, inflight=None, status="error"):
    inflight = inflight or {}
    result = {
        "stock_code": code,
        "trading_date": inflight.get("trading_date"),
        "minute_recovery_status": status,
        "minute_recovery_error": str(message)[:200],
        "minute_recovery_requested_at": inflight.get("requested_at"),
        "minute_recovery_completed_at": now_text(),
        "minute_recovery_snapshot_at": now_text(),
        "minute_recovery_market_scope": inflight.get("market_scope"),
        "recovery_values": {},
    }
    provider._minute_recovery_last_error = result["minute_recovery_error"]
    provider._minute_recovery_last_result = dict(result)
    provider._minute_recovery_cache[code] = dict(result)
    if provider.store is not None:
        provider.store.update_close_metrics(code, result)
    return result


class AfterCloseRecoveryCoordinator:
    def __init__(self, base, provider, scheduler_module):
        from realtime_v2.offhours_metric_completion_patch import (
            OffhoursMetricCompletionDrain,
        )

        self.delegate = OffhoursMetricCompletionDrain
        self.delegate.__init__(self, base, provider, scheduler_module)
        self.controller = "after_close_recovery_coordinator_v1"
        self.refresh_sec = max(
            2.0,
            float(os.getenv("STOCKBOARD_AFTER_CLOSE_REFRESH_SEC", "5")),
        )
        self.global_backoff_sec = max(
            60.0,
            float(os.getenv("STOCKBOARD_AFTER_CLOSE_GLOBAL_BACKOFF_SEC", "1800")),
        )
        self.global_error_limit = max(
            1,
            int(os.getenv("STOCKBOARD_AFTER_CLOSE_GLOBAL_ERROR_LIMIT", "3")),
        )
        self.cutoff_sec = max(
            60.0,
            float(os.getenv("STOCKBOARD_AFTER_CLOSE_CUTOFF_SEC", "300")),
        )
        self.theme_url = "http://127.0.0.1:8765/api/v2/themes/snapshot"
        self.detail_url = "http://127.0.0.1:8765/api/v2/themes/detail?theme_id="
        self.plan = []
        self.rows_by_code = {}
        self.lane_by_code = {}
        self.theme_details = {}
        self.theme_version = None
        self.completed = set()
        self.global_errors = 0
        self.global_backoff_until = 0.0
        self.global_backoff_until_iso = None
        self.minute_request_count = 0
        self.minute_success_count = 0
        self.minute_error_count = 0
        self.minute_krx_fallback_count = 0
        self.current_lane = None
        self.current_scope = None
        self.program_bundle_source = "existing_context_program_batch"
        self.ohlc_bundle_source = "minute_bar_fallback_and_existing_ohlc_snapshot"

    def _retry_delay(self, attempt):
        return RETRY_SEC[min(max(1, int(attempt)) - 1, 2)]

    def target_date(self):
        return str(last_completed_trading_date(datetime.now()) or "")

    def _theme_data(self):
        try:
            theme = self.scheduler_module._read_json_url(self.theme_url)
        except Exception:
            return {}, {}
        version = theme.get("cache_version")
        if version != self.theme_version:
            self.theme_details = {}
            self.theme_version = version
        details = {}
        for item in theme.get("themes", []) or []:
            theme_id = str(item.get("theme_id") or "")
            if theme_id not in self.theme_details:
                try:
                    self.theme_details[theme_id] = self.scheduler_module._read_json_url(
                        self.detail_url + urlquote(theme_id)
                    )
                except Exception:
                    self.theme_details[theme_id] = {}
            details[theme_id] = self.theme_details[theme_id]
        return theme, details

    def _build_plan(self, payload):
        rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
        by_code = {
            self.base.normalize_code(row.get("stock_code")): row
            for row in rows
        }
        theme, details = self._theme_data()
        themes = theme.get("themes", []) or []
        result = []
        seen = set()

        def add(value, lane, reason):
            code = self.base.normalize_code(value)
            if code and code not in seen:
                seen.add(code)
                result.append(
                    {
                        "stock_code": code,
                        "lane": lane,
                        "reason": reason,
                        "row": by_code.get(code, {}),
                    }
                )

        add(self.scheduler_module._load_selected(self.base), 0, "s1")
        for row in rows[:20]:
            add(row.get("stock_code"), 1, "top20")
        for item in themes[:5]:
            for leader in item.get("leaders", []) or []:
                add(
                    leader.get("stock_code") if isinstance(leader, dict) else None,
                    2,
                    "top_theme_leader",
                )
        for offset, theme_items in ((0, themes[:5]), (1, themes[5:])):
            for item in theme_items:
                detail = details.get(str(item.get("theme_id") or "")) or {}
                for member in detail.get("members", []) or []:
                    add(
                        member.get("stock_code") if isinstance(member, dict) else None,
                        3 + offset,
                        "theme_member",
                    )
        for index, row in enumerate(rows[20:], 21):
            if 21 <= self.scheduler_module._model_rank(row, index) <= 50:
                add(row.get("stock_code"), 5, "hidden50")
        for row in rows:
            add(row.get("stock_code"), 6, "top300")
        return result

    def _needs(self, row):
        minute_ok = str(row.get("minute_recovery_status") or "").lower() == "ok"
        strength_5m, execution, orderbook = self.delegate._row_needs(self, row)
        return not minute_ok, strength_5m or execution, orderbook, strength_5m, execution

    def _refresh(self, now_mono):
        if now_mono - self.last_refresh_at < self.refresh_sec:
            return
        self.last_refresh_at = now_mono
        try:
            payload = self.scheduler_module._read_json_url(self.url)
            self.plan = self._build_plan(payload)
        except Exception as error:
            self.mode = "snapshot_error"
            self.last_error = str(error)
            return
        self.rows_by_code = {item["stock_code"]: item["row"] for item in self.plan}
        self.lane_by_code = {item["stock_code"]: item["lane"] for item in self.plan}
        tasks = []
        targets = set()
        minute_missing = 0
        strength_missing = 0
        execution_missing = 0
        order_missing = 0
        for item in self.plan:
            code = item["stock_code"]
            lane = item["lane"]
            row = item["row"]
            need_minute, need_strength, need_order, strength_5m, execution = self._needs(row)
            minute_missing += int(need_minute)
            strength_missing += int(strength_5m)
            execution_missing += int(execution)
            if need_minute:
                tasks.append((lane, 0, ("minute", code)))
                targets.add(("minute", code))
            if need_strength:
                tasks.append((lane, 1, ("strength", code)))
                targets.add(("strength", code))
            if need_order and lane <= 3:
                order_missing += 1
                tasks.append((lane, 4, ("orderbook", code)))
                targets.add(("orderbook", code))
        self.total_count = len(self.plan)
        self.minute_missing_count = minute_missing
        self.missing_count = strength_missing
        self.missing_strength5_count = strength_missing
        self.missing_execution_count = execution_missing
        self.missing_orderbook_count = order_missing
        existing = set(self.queue)
        if self.current:
            existing.add(self.current)
        for _lane, _order, task in sorted(
            tasks,
            key=lambda value: (value[0], value[1], value[2][1]),
        ):
            if task in existing:
                continue
            if (task[0], task[1], self.target_date()) in self.completed:
                continue
            if now_mono < self.retry_at.get(task, 0):
                continue
            if now_mono < self.settle_until.get(task, 0):
                continue
            self.queue.append(task)
            existing.add(task)
        if not targets:
            self.mode = "complete"
        elif not self.queue and not self.current:
            self.mode = "retry_wait"

    def _cache_result(self, kind, code):
        if kind != "minute":
            return self.delegate._cache_result(self, kind, code)
        with self.provider._lock:
            cached = self.provider._minute_recovery_cache.get(code)
            last = self.provider._minute_recovery_last_result
            if isinstance(cached, dict):
                return dict(cached)
            if isinstance(last, dict) and str(last.get("stock_code") or "") == code:
                return dict(last)
            return {}

    def _inflight(self, kind):
        if kind != "minute":
            return self.delegate._inflight(self, kind)
        with self.provider._lock:
            value = self.provider._minute_recovery_inflight
            return dict(value) if isinstance(value, dict) else None

    def _clear_inflight(self, kind, code):
        if kind != "minute":
            return self.delegate._clear_inflight(self, kind, code)
        with self.provider._lock:
            value = self.provider._minute_recovery_inflight
            if isinstance(value, dict) and str(value.get("stock_code") or "") == code:
                self.provider._minute_recovery_inflight = None

    def _record_error(self, kind, code, message, status="error"):
        if kind != "minute":
            return self.delegate._record_error(self, kind, code, message, status)
        minute_error(
            self.provider,
            code,
            message,
            {
                "trading_date": self.current_trading_date,
                "requested_at": self.current_requested_at,
                "market_scope": self.current_scope,
            },
            status,
        )

    def _publish_meta(self, kind, code, result):
        values = {}
        trading_date = result.get("trading_date") or self.target_date()
        if kind == "strength":
            basis = (
                result.get("strength_completed_at")
                or result.get("strength_snapshot_at")
                or now_text()
            )
            for field, source_field in (
                ("execution_strength", "realtime_strength_snapshot"),
                ("strength_5m", "strength_5m"),
                ("strength_20m", "strength_20m"),
                ("strength_60m", "strength_60m"),
            ):
                if result.get(source_field) not in (None, ""):
                    values[field] = source_value(
                        result[source_field],
                        "OPT10046",
                        "EXACT",
                        basis,
                        trading_date,
                        "AL_OR_DECLARED",
                        0.95,
                        False,
                    )
        elif kind == "orderbook":
            value = result.get("bid_ask_ratio_snapshot") or result.get("bid_ask_ratio")
            if value not in (None, ""):
                values["bid_ask_ratio"] = source_value(
                    value,
                    "OPT10004",
                    "EXACT",
                    result.get("orderbook_completed_at")
                    or result.get("orderbook_snapshot_at")
                    or now_text(),
                    trading_date,
                    "AL_OR_DECLARED",
                    0.95,
                    False,
                )
        if values and self.provider.store is not None:
            self.provider.store.update_close_metrics(code, {"recovery_values": values})

    def _backoff(self):
        self.global_backoff_until = time.monotonic() + self.global_backoff_sec
        self.global_backoff_until_iso = (
            datetime.now() + timedelta(seconds=self.global_backoff_sec)
        ).isoformat(timespec="seconds")
        self.global_errors = 0
        self.mode = "global_backoff"

    def _finish(self, forced_status=None):
        task = self.current
        if not task:
            return
        kind, code = task
        if kind != "minute":
            result = self._cache_result(kind, code)
            self.delegate._finish(self, forced_status)
            if self.last_status == "ok":
                self._publish_meta(kind, code, result)
                self.completed.add((kind, code, self.target_date()))
                self.global_errors = 0
            elif self.last_status in {"error", "timeout", "failed"}:
                self.global_errors += 1
                if self.global_errors >= self.global_error_limit:
                    self._backoff()
            return
        result = self._cache_result(kind, code)
        status = str(
            forced_status or result.get("minute_recovery_status") or "no_data"
        ).lower()
        attempt = max(1, int(self.attempts.get(task, 1)))
        if status == "ok" and result.get("recovery_values"):
            self.minute_success_count += 1
            self.success_count += 1
            self.completed.add((kind, code, self.target_date()))
            self.retry_at.pop(task, None)
            self.global_errors = 0
        else:
            self.minute_error_count += 1
            delay = 5 if attempt == 1 else self._retry_delay(attempt - 1)
            self.retry_at[task] = time.monotonic() + delay
            if attempt > 1:
                self.global_errors += 1
                if self.global_errors >= self.global_error_limit:
                    self._backoff()
        self.last_code = code
        self.last_kind = kind
        self.last_status = status
        self.current = None
        self.current_requested_at = None
        self.current_trading_date = None
        self.current_started = 0.0
        self.current_scope = None
        self.current_lane = None
        self.next_request_at = time.monotonic() + self.gap_sec
        self.last_refresh_at = 0.0

    def _other_tr_busy(self):
        ready, reason = provider_tr_idle(self.provider)
        if ready or reason.startswith("global_gap"):
            return None
        return reason

    def _send_minute(self, task, now_mono):
        _kind, code = task
        ready, reason = provider_tr_idle(self.provider, self.gap_sec)
        if not ready:
            self.mode = f"waiting_other_tr:{reason}"
            return
        attempt = int(self.attempts.get(task, 0)) + 1
        if attempt == 1:
            scope = "AL"
            request_code = f"{code}_AL"
        else:
            scope = "KRX"
            request_code = code
            self.minute_krx_fallback_count += 1
        requested_at = now_text()
        trading_date = self.target_date()
        with self.provider._lock:
            control = self.provider._control
            if control is None:
                self.mode = "waiting_provider:control_unavailable"
                return
            self.provider._minute_recovery_inflight = {
                "stock_code": code,
                "request_code": request_code,
                "market_scope": scope,
                "trading_date": trading_date,
                "requested_at": requested_at,
                "started_at_monotonic": now_mono,
                "rqname": MINUTE_RQNAME,
                "trcode": MINUTE_TRCODE,
                "screen_no": MINUTE_SCREEN,
                "owner": self.controller,
            }
            self.provider._minute_recovery_last_request_at = now_mono
        self.current = task
        self.current_started = now_mono
        self.current_requested_at = requested_at
        self.current_trading_date = trading_date
        self.current_scope = scope
        self.current_lane = self.lane_by_code.get(code)
        self.attempts[task] = attempt
        self.request_count += 1
        self.minute_request_count += 1
        self.last_code = code
        self.last_kind = "minute"
        try:
            control.dynamicCall(
                "SetInputValue(QString, QString)",
                "종목코드",
                request_code,
            )
            control.dynamicCall("SetInputValue(QString, QString)", "틱범위", "1")
            control.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            result = control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                MINUTE_RQNAME,
                MINUTE_TRCODE,
                0,
                MINUTE_SCREEN,
            )
            if result not in (None, 0, "0"):
                raise RuntimeError(f"CommRqData returned {result!r}")
        except Exception as error:
            self._clear_inflight("minute", code)
            self._record_error("minute", code, str(error))
            self.last_error = str(error)
            self._finish("error")
            return
        self.mode = "minute_inflight"

    def _send(self, task, row, now_mono):
        if task[0] == "minute":
            return self._send_minute(task, now_mono)
        self.current_lane = self.lane_by_code.get(task[1])
        return self.delegate._send(self, task, row, now_mono)

    def _purge_pending(self):
        with self.provider._lock:
            for pending_name, codes_name in (
                ("_strength_probe_pending", "_strength_probe_pending_codes"),
                ("_orderbook_probe_pending", "_orderbook_probe_pending_codes"),
            ):
                pending = getattr(self.provider, pending_name, None)
                self.pending_purge_count += len(pending or ())
                if pending is not None:
                    pending.clear()
                codes = getattr(self.provider, codes_name, None)
                if hasattr(codes, "clear"):
                    codes.clear()

    def _active(self, session, now):
        phase = str(getattr(session, "phase", "") or "").lower()
        if phase not in AFTER_CLOSE_PHASES:
            return False, "inactive_session"
        next_open = next_premarket_datetime(now)
        self.next_premarket_at = next_open.isoformat(timespec="seconds")
        if (next_open - now).total_seconds() <= self.cutoff_sec:
            return False, "premarket_cutoff"
        if phase == "closed" and getattr(session, "is_trading_day", False):
            end = clock_at(
                now,
                (getattr(session, "windows", {}) or {}).get("aftermarket_end"),
                "20:00",
            )
            if now < end + timedelta(minutes=3):
                return False, "waiting_20_03"
        return True, "active"

    def _gap(self, session, now):
        if (next_premarket_datetime(now) - now).total_seconds() <= 1800:
            return 3.0
        phase = str(getattr(session, "phase", "") or "").lower()
        if phase in {"weekend", "holiday", "before_market"} or now.hour >= 21:
            return 10.0
        return 3.0

    def tick(self):
        self.last_tick_at = now_text()
        if not self.enabled:
            return
        session = self.scheduler_module.market_session_now()
        current = datetime.now()
        self.phase = str(getattr(session, "phase", "unknown") or "unknown").lower()
        active, reason = self._active(session, current)
        if not active:
            self.mode = reason
            return
        with self.provider._lock:
            ready = (
                self.provider._running
                and self.provider._login_state == "connected"
                and self.provider._control is not None
            )
        if not ready:
            self.mode = "waiting_provider"
            return
        self.gap_sec = self._gap(session, current)
        self._purge_pending()
        now_mono = time.monotonic()
        self._observe(now_mono)
        if self.current:
            return
        self._refresh(now_mono)
        if now_mono < self.global_backoff_until:
            self.mode = "global_backoff"
            return
        if now_mono < self.next_request_at:
            self.mode = "rate_gap"
            return
        while self.queue:
            task = self.queue.popleft()
            if now_mono < self.retry_at.get(task, 0):
                continue
            self._send(task, self.rows_by_code.get(task[1], {}), now_mono)
            return
        if not (
            getattr(self, "minute_missing_count", 0)
            or self.missing_strength5_count
            or self.missing_execution_count
            or self.missing_orderbook_count
        ):
            self.mode = "complete"
        else:
            self.mode = "retry_wait"

    def stats(self):
        result = self.delegate.stats(self)
        result.update(
            {
                "controller": self.controller,
                "current_lane": self.current_lane,
                "current_scope": self.current_scope,
                "minute_missing_count": int(getattr(self, "minute_missing_count", 0)),
                "minute_request_count": self.minute_request_count,
                "minute_success_count": self.minute_success_count,
                "minute_error_count": self.minute_error_count,
                "minute_krx_fallback_count": self.minute_krx_fallback_count,
                "global_backoff_until": self.global_backoff_until_iso,
                "global_consecutive_errors": self.global_errors,
                "next_premarket_at": getattr(self, "next_premarket_at", None),
                "program_bundle_source": self.program_bundle_source,
                "ohlc_bundle_source": self.ohlc_bundle_source,
                "source_metadata_contract": (
                    "value/source/status/basis_time/trading_date/market_scope/"
                    "quality/is_estimated/updated_at/coverage"
                ),
            }
        )
        return result


def coordinator_class():
    from realtime_v2.offhours_metric_completion_patch import (
        OffhoursMetricCompletionDrain,
    )

    class Coordinator(AfterCloseRecoveryCoordinator, OffhoursMetricCompletionDrain):
        pass

    Coordinator.__name__ = "AfterCloseRecoveryCoordinator"
    return Coordinator


def prepare_collector():
    from realtime_v2 import strength5m_definitive_preopen_patch as definitive

    if getattr(definitive, "_after_close_recovery_prepared", False):
        return
    definitive.OffhoursStrengthDrain = coordinator_class()
    definitive._after_close_recovery_prepared = True


def install_collector(base):
    if getattr(base, "_after_close_recovery_collector_installed", False):
        return
    prepare_collector()
    provider_class = base.KiwoomOpenApiRealtimeProvider
    original_init = provider_class.__init__
    original_handle = provider_class._handle_receive_tr_data
    original_status = provider_class.status

    def provider_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._minute_recovery_inflight = None
        self._minute_recovery_cache = {}
        self._minute_recovery_last_request_at = 0.0
        self._minute_recovery_last_result = None
        self._minute_recovery_last_error = None
        self._minute_recovery_pending = deque()

    def handle(self, *args):
        if len(args) >= 3 and str(args[1]) == MINUTE_RQNAME:
            screen = str(args[0])
            rq_name = str(args[1])
            tr_code = str(args[2])
            record = str(args[3]) if len(args) > 3 else ""
            with self._lock:
                inflight = self._minute_recovery_inflight
                self._minute_recovery_inflight = None
            if not isinstance(inflight, dict):
                return
            code = str(inflight.get("stock_code") or "")
            try:
                result = minute_result(
                    self,
                    inflight,
                    tr_code,
                    rq_name,
                    record,
                    screen,
                )
            except Exception as error:
                minute_error(self, code, str(error), inflight)
            else:
                self._minute_recovery_cache[code] = dict(result)
                self._minute_recovery_last_result = dict(result)
                self._minute_recovery_last_error = result.get("minute_recovery_error")
                if self.store is not None:
                    self.store.update_close_metrics(code, result)
            return
        return original_handle(self, *args)

    def status(self):
        result = original_status(self)
        result = result if isinstance(result, dict) else {"status": result}
        with self._lock:
            result["minute_bar_recovery"] = {
                "inflight_code": self._minute_recovery_inflight.get("stock_code")
                if isinstance(self._minute_recovery_inflight, dict)
                else None,
                "cache_size": len(self._minute_recovery_cache),
                "last_request_at_monotonic": self._minute_recovery_last_request_at,
                "last_result": deepcopy(self._minute_recovery_last_result),
                "last_error": self._minute_recovery_last_error,
                "rqname": MINUTE_RQNAME,
                "trcode": MINUTE_TRCODE,
                "screen_no": MINUTE_SCREEN,
            }
        drain = getattr(self, "_stockboard_offhours_strength_drain", None)
        if drain:
            result["after_close_recovery"] = drain.stats()
        return result

    provider_class.__init__ = provider_init
    provider_class._handle_receive_tr_data = handle
    provider_class.status = status
    provider_class.stockboard_tr_idle = lambda self, gap=0: provider_tr_idle(self, gap)

    try:
        import realtime_v2.strength5m_scheduler as strength

        original_strength_idle = strength.Strength5mScheduler._idle

        def strength_idle(self, session=None, gap_override=None):
            checker = getattr(self.provider, "stockboard_tr_idle", None)
            if checker:
                gap = float(
                    gap_override if gap_override is not None else self.gap(session)
                )
                return checker(gap)[0]
            return original_strength_idle(self, session, gap_override)

        strength.Strength5mScheduler._idle = strength_idle
    except Exception:
        pass

    try:
        import realtime_v2.orderbook_thin_scheduler as orderbook

        original_order_idle = orderbook.OrderbookThinScheduler._idle

        def order_idle(self, session=None):
            checker = getattr(self.provider, "stockboard_tr_idle", None)
            if checker:
                return checker(self.gap(session))[0]
            return original_order_idle(self, session)

        orderbook.OrderbookThinScheduler._idle = order_idle
    except Exception:
        pass

    base._after_close_recovery_collector_installed = True
