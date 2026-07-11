from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from realtime_v2.market_session import market_session_now, next_premarket_datetime
from stockboard_theme_engine import ThemeEngine, load_theme_master

FIELDS = (
    "stock_code", "stock_name",
    "price", "trade_price", "day_close", "seed_price", "ohlc",
    "change_rate", "seed_change_rate",
    "trade_value_eok", "seed_trade_value_eok", "prev_trade_value_eok",
    "amount_ratio",
    "execution_strength", "last_valid_execution_strength",
    "strength_5m", "last_valid_strength_5m", "regular_close_strength_1m", "one_min_strength",
    "program_net", "large_trade_net_count",
    "received_at", "price_age_sec", "strength_age_sec", "program_age_sec",
)
HOLD_PHASES = {"before_market", "after_wait", "closed", "weekend", "holiday"}
PERSIST_PHASES = {"after_wait", "aftermarket", "closed", "weekend", "holiday"}


def first_value(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_iso(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class ThemeCacheService(threading.Thread):
    def __init__(
        self,
        state,
        master_path,
        *,
        persist_path=None,
        interval_sec: float = 0.5,
        slow_interval_sec: float = 1.0,
        slow_threshold_ms: float = 30.0,
        now_provider=None,
    ):
        super().__init__(name="stockboard-v2-theme-cache", daemon=True)
        self.state = state
        self.master_path = Path(master_path)
        self.persist_path = Path(persist_path) if persist_path else None
        self.interval = max(0.5, float(interval_sec))
        self.slow = max(self.interval, float(slow_interval_sec))
        self.threshold = float(slow_threshold_ms)
        self.now_provider = now_provider or datetime.now

        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.RLock()

        self.client_count = 0
        self.cache_version = 0
        self.last_source_version = None
        self.last_phase = None
        self.last_check_mono = 0.0
        self.last_compute_mono = 0.0
        self.last_compute_ms = None
        self.last_lock_ms = None
        self.last_lock_wait_ms = 0.0
        self.last_lock_probe_ms = None
        self.last_copy_ms = None
        self.lock_busy_skip_count = 0
        self.last_payload_bytes = 0
        self.last_error = None
        self.last_updated_at = None
        self.compute_attempt_count = 0
        self.compute_success_count = 0
        self.compute_skip_count = 0
        self.last_persist_mono = 0.0
        self.close_hold_active = False
        self.hold_until = None

        self.enabled = False
        self.master = None
        self.engine = None
        self.details = {}
        self.detail_bytes = {}

        try:
            self.master = load_theme_master(self.master_path)
            self.engine = ThemeEngine(self.master, display_theme_limit=10)
            self.enabled = True
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"

        initial_state = "WAIT_CLIENT" if self.enabled else "INVALID_MASTER"
        self.snapshot = self.status_payload(initial_state)
        self.snapshot_bytes = self.encode(self.snapshot)
        self._load_close_snapshot()

    @staticmethod
    def encode(payload):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def now_text():
        return datetime.now().isoformat(timespec="milliseconds")

    def current_datetime(self):
        value = self.now_provider()
        return value if isinstance(value, datetime) else datetime.now()

    def current_session(self):
        try:
            return market_session_now(self.current_datetime()).to_dict()
        except Exception:
            status = getattr(self.state, "status", {}) or {}
            return {
                "phase": status.get("market_phase"),
                "phase_label": status.get("market_phase_label"),
                "trading_date": status.get("market_trading_date"),
                "is_trading_day": True,
                "windows": {"premarket_start": "08:00"},
            }

    def market_session(self):
        session = self.current_session()
        return {"phase": session.get("phase"), "phase_label": session.get("phase_label")}

    def cache_age_ms(self):
        if self.last_compute_mono <= 0:
            return None
        return max(0, round((time.monotonic() - self.last_compute_mono) * 1000))

    def status_payload(self, state_name):
        return {
            "schema_version": 1,
            "source": "stockboard_v2_theme_cache",
            "ts": self.now_text(),
            "cache_version": self.cache_version,
            "market_session": self.market_session(),
            "status": {
                "state": state_name,
                "enabled": self.enabled,
                "theme_clients": self.client_count,
                "cache_age_ms": self.cache_age_ms(),
                "compute_ms": self.last_compute_ms,
                "lock_ms": self.last_lock_ms,
                "lock_wait_ms": self.last_lock_wait_ms,
                "lock_probe_ms": self.last_lock_probe_ms,
                "copy_ms": self.last_copy_ms,
                "lock_busy_skip_count": self.lock_busy_skip_count,
                "payload_bytes": self.last_payload_bytes,
                "last_error": self.last_error,
                "compute_attempt_count": self.compute_attempt_count,
                "compute_success_count": self.compute_success_count,
                "compute_skip_count": self.compute_skip_count,
                "display_basis": "WAIT",
                "display_basis_text": "테마 데이터 대기",
                "hold_until": None,
            },
            "summary": {
                "top5_concentration_text": "-",
                "strongest_flow_text": "-",
                "breadth_text": "-",
                "warning_text": self.last_error or "테마 데이터 대기",
                "warning_tone": "warn" if self.last_error else "zero",
            },
            "themes": [],
        }

    def register_client(self):
        with self.lock:
            self.client_count += 1
        self.wake.set()

    def unregister_client(self):
        with self.lock:
            self.client_count = max(0, self.client_count - 1)

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def source_version_hint(self):
        status = getattr(self.state, "status", {}) or {}
        trade_count = status.get("trade_count")
        if trade_count is None:
            trade_count = status.get("event_count")
        session = self.current_session()
        return (
            int(trade_count or 0),
            str(status.get("program_net_last_at") or ""),
            str(session.get("phase") or ""),
            str(session.get("trading_date") or ""),
        )

    def _resolved_row(self, quote, hold_phase):
        raw = {key: quote.get(key) for key in FIELDS}
        ohlc = raw.get("ohlc") if isinstance(raw.get("ohlc"), dict) else {}
        price = first_value(raw, "price", "trade_price", "day_close", "seed_price")
        if price in (None, ""):
            price = ohlc.get("close")
        trade_value = first_value(raw, "trade_value_eok", "seed_trade_value_eok")
        previous_value = raw.get("prev_trade_value_eok")
        amount_ratio = raw.get("amount_ratio")
        try:
            if amount_ratio in (None, "") and float(previous_value or 0) > 0 and trade_value not in (None, ""):
                amount_ratio = float(trade_value) / float(previous_value)
        except (TypeError, ValueError, ZeroDivisionError):
            amount_ratio = None
        return {
            "stock_code": raw.get("stock_code"),
            "stock_name": raw.get("stock_name"),
            "price": price,
            "change_rate": first_value(raw, "change_rate", "seed_change_rate"),
            "trade_value_eok": trade_value,
            "amount_ratio": amount_ratio,
            "execution_strength": first_value(raw, "execution_strength", "last_valid_execution_strength"),
            "strength_5m": first_value(
                raw,
                "strength_5m",
                "last_valid_strength_5m",
                "regular_close_strength_1m",
                "one_min_strength",
            ),
            "program_net": raw.get("program_net"),
            "large_trade_net_count": raw.get("large_trade_net_count"),
            "received_at": raw.get("received_at"),
            "price_age_sec": None if hold_phase else raw.get("price_age_sec"),
            "strength_age_sec": None if hold_phase else raw.get("strength_age_sec"),
            "program_age_sec": None if hold_phase else raw.get("program_age_sec"),
        }

    def copy_rows(self, hold_phase=False):
        state_lock = getattr(self.state, "lock", None)
        probe_started = time.perf_counter()
        if state_lock:
            try:
                acquired = state_lock.acquire(blocking=False)
            except TypeError:
                acquired = state_lock.acquire(False)
        else:
            acquired = True
        probe_ms = (time.perf_counter() - probe_started) * 1000
        if not acquired:
            return [], self.source_version_hint(), 0.0, probe_ms, 0.0, False

        copy_started = time.perf_counter()
        try:
            quotes = getattr(self.state, "quotes", {}) or {}
            source_version = self.source_version_hint()
            rows = [self._resolved_row(quote, hold_phase) for quote in quotes.values()]
        finally:
            if state_lock:
                state_lock.release()
        copy_ms = (time.perf_counter() - copy_started) * 1000
        return rows, source_version, 0.0, probe_ms, copy_ms, True

    def _next_premarket(self, now):
        session = self.current_session()
        if str(session.get("phase") or "") == "before_market" and session.get("is_trading_day") is True:
            time_text = str((session.get("windows") or {}).get("premarket_start") or "08:00")
            try:
                hour_text, minute_text = time_text.split(":", 1)
                return now.replace(hour=int(hour_text), minute=int(minute_text[:2]), second=0, microsecond=0)
            except (TypeError, ValueError):
                return now.replace(hour=8, minute=0, second=0, microsecond=0)
        return next_premarket_datetime(now)

    def _close_payload(self, payload, details, session, hold_until):
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        status = dict(payload.get("status") or {})
        status.update(
            state="READY",
            display_basis="LAST_CLOSE",
            display_basis_text="장마감 마지막값 유지",
            hold_until=hold_until.isoformat(timespec="minutes"),
        )
        payload["status"] = status
        payload["market_session"] = {
            "phase": session.get("phase"),
            "phase_label": session.get("phase_label"),
        }
        payload["display_basis"] = "LAST_CLOSE"
        next_details = {}
        for theme_id, detail in (details or {}).items():
            item = json.loads(json.dumps(detail, ensure_ascii=False))
            item["display_basis"] = "LAST_CLOSE"
            item["hold_until"] = hold_until.isoformat(timespec="minutes")
            next_details[theme_id] = item
        return payload, next_details

    def _persist_current(self, *, force=False, session=None):
        if not self.persist_path or not self.snapshot.get("themes"):
            return False
        now_mono = time.monotonic()
        if not force and now_mono - self.last_persist_mono < 60:
            return False
        now = self.current_datetime()
        session = session or self.current_session()
        phase = str(session.get("phase") or "")
        if phase not in PERSIST_PHASES and not self.close_hold_active:
            return False
        try:
            hold_until = self._next_premarket(now)
        except Exception:
            return False
        record = {
            "schema_version": 1,
            "saved_at": now.isoformat(timespec="seconds"),
            "valid_until": hold_until.isoformat(timespec="seconds"),
            "master_version": self.snapshot.get("master_version"),
            "snapshot": self.snapshot,
            "details": self.details,
        }
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
            temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.persist_path)
            self.last_persist_mono = now_mono
            self.hold_until = hold_until
            return True
        except OSError as error:
            self.last_error = f"theme close snapshot save failed: {error}"
            return False

    def _load_close_snapshot(self):
        if not self.persist_path or not self.persist_path.is_file() or not self.enabled:
            return False
        session = self.current_session()
        if str(session.get("phase") or "") not in HOLD_PHASES:
            return False
        try:
            record = json.loads(self.persist_path.read_text(encoding="utf-8-sig"))
            valid_until = parse_iso(record.get("valid_until"))
            now = self.current_datetime()
            if valid_until is None or now >= valid_until:
                return False
            snapshot = record.get("snapshot")
            details = record.get("details")
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("themes"), list):
                return False
            if not isinstance(details, dict):
                details = {}
            snapshot, details = self._close_payload(snapshot, details, session, valid_until)
            self.cache_version = max(1, int(snapshot.get("cache_version") or 1))
            snapshot["cache_version"] = self.cache_version
            self.snapshot = snapshot
            self.snapshot_bytes = self.encode(snapshot)
            self.details = details
            self.detail_bytes = {key: self.encode(value) for key, value in details.items()}
            self.last_payload_bytes = len(self.snapshot_bytes)
            self.last_updated_at = snapshot.get("ts") or record.get("saved_at")
            self.last_compute_mono = time.monotonic()
            self.last_source_version = self.source_version_hint()
            self.close_hold_active = True
            self.hold_until = valid_until
            self.last_phase = str(session.get("phase") or "")
            return True
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False

    def _enter_close_hold(self, session):
        if not self.snapshot.get("themes"):
            return False
        if self.close_hold_active and self.last_phase == session.get("phase"):
            return True
        try:
            hold_until = self._next_premarket(self.current_datetime())
        except Exception:
            return False
        payload, details = self._close_payload(self.snapshot, self.details, session, hold_until)
        self.cache_version += 1
        payload["cache_version"] = self.cache_version
        for detail in details.values():
            detail["cache_version"] = self.cache_version
        with self.lock:
            self.snapshot = payload
            self.snapshot_bytes = self.encode(payload)
            self.details = details
            self.detail_bytes = {key: self.encode(value) for key, value in details.items()}
            self.last_payload_bytes = len(self.snapshot_bytes)
            self.close_hold_active = True
            self.hold_until = hold_until
            self.last_source_version = self.source_version_hint()
        self._persist_current(force=True, session=session)
        return True

    def _leave_close_hold(self):
        if not self.close_hold_active:
            return
        self.close_hold_active = False
        self.hold_until = None
        self.last_source_version = None
        if self.master is not None:
            self.engine = ThemeEngine(self.master, display_theme_limit=10)
        try:
            if self.persist_path and self.persist_path.is_file():
                self.persist_path.unlink()
        except OSError:
            pass

    def refresh(self, *, force=False):
        if not self.enabled or not self.engine:
            return False

        session = self.current_session()
        phase = str(session.get("phase") or "")
        hold_phase = self.persist_path is not None and phase in HOLD_PHASES
        if self.close_hold_active and not hold_phase:
            self._leave_close_hold()
        if hold_phase and self.close_hold_active:
            if self.last_phase != phase:
                self.last_phase = phase
                self._enter_close_hold(session)
            self.compute_skip_count += 1
            return False

        now = time.monotonic()
        interval = self.slow if (self.last_compute_ms or 0) > self.threshold else self.interval
        if not force and now - self.last_check_mono < interval:
            self.compute_skip_count += 1
            return False
        self.last_check_mono = now

        hinted_source_version = self.source_version_hint()
        if not force and self.last_source_version is not None and hinted_source_version == self.last_source_version:
            self.compute_skip_count += 1
            return False

        self.compute_attempt_count += 1
        rows, source_version, wait_ms, probe_ms, copy_ms, acquired = self.copy_rows(hold_phase=hold_phase)
        self.last_lock_wait_ms = wait_ms
        self.last_lock_probe_ms = probe_ms
        if not acquired:
            self.lock_busy_skip_count += 1
            self.compute_skip_count += 1
            return False

        self.last_lock_ms = copy_ms
        self.last_copy_ms = copy_ms
        compute_started = time.perf_counter()
        try:
            result = self.engine.compute(rows, now_mono=now, market_session=self.market_session())
            compute_ms = (time.perf_counter() - compute_started) * 1000
            version = self.cache_version + 1
            diagnostics = result.pop("diagnostics", {})
            raw_details = result.pop("details", {})
            display_basis = "LAST_CLOSE" if hold_phase else "LIVE"
            basis_text = "장마감 마지막값 유지" if hold_phase else "실시간"
            result.update(
                ts=self.now_text(),
                cache_version=version,
                display_basis=display_basis,
                status={
                    "state": "READY",
                    "enabled": True,
                    "theme_count": diagnostics.get("theme_count", 0),
                    "display_theme_count": diagnostics.get("display_theme_count", 0),
                    "cache_age_ms": 0,
                    "compute_ms": round(compute_ms, 3),
                    "lock_ms": round(copy_ms, 3),
                    "lock_wait_ms": 0.0,
                    "lock_probe_ms": round(probe_ms, 3),
                    "copy_ms": round(copy_ms, 3),
                    "lock_busy_skip_count": self.lock_busy_skip_count,
                    "payload_bytes": 0,
                    "theme_clients": self.client_count,
                    "negative_delta_count": diagnostics.get("negative_delta_count", 0),
                    "compute_attempt_count": self.compute_attempt_count,
                    "compute_success_count": self.compute_success_count + 1,
                    "compute_skip_count": self.compute_skip_count,
                    "last_error": None,
                    "display_basis": display_basis,
                    "display_basis_text": basis_text,
                    "hold_until": None,
                },
            )
            details = {}
            for theme_id, detail in raw_details.items():
                item = dict(detail)
                item.update(cache_version=version, ts=result["ts"], display_basis=display_basis)
                details[theme_id] = item
            body = self.encode(result)
            result["status"]["payload_bytes"] = len(body)
            body = self.encode(result)

            with self.lock:
                self.cache_version = version
                self.snapshot = result
                self.snapshot_bytes = body
                self.details = details
                self.detail_bytes = {key: self.encode(value) for key, value in details.items()}
                self.last_source_version = source_version
                self.last_compute_mono = time.monotonic()
                self.last_compute_ms = compute_ms
                self.last_lock_ms = copy_ms
                self.last_lock_wait_ms = 0.0
                self.last_lock_probe_ms = probe_ms
                self.last_copy_ms = copy_ms
                self.last_payload_bytes = len(body)
                self.last_error = None
                self.last_updated_at = result["ts"]
                self.compute_success_count += 1
                self.last_phase = phase

            if phase in PERSIST_PHASES:
                self._persist_current(force=phase in {"after_wait", "closed"}, session=session)
            if hold_phase:
                self._enter_close_hold(session)
            return True
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            if not self.snapshot.get("themes"):
                self.snapshot = self.status_payload("ERROR")
                self.snapshot_bytes = self.encode(self.snapshot)
            return False

    def get_snapshot(self, force_if_empty=True):
        if force_if_empty and self.cache_version <= 0:
            self.refresh(force=True)
        with self.lock:
            payload = dict(self.snapshot)
            payload["status"] = dict(payload.get("status") or {})
            payload["status"]["cache_age_ms"] = self.cache_age_ms()
            return payload, self.snapshot_bytes

    def get_detail(self, theme_id):
        if self.cache_version <= 0:
            self.refresh(force=True)
        with self.lock:
            return self.details.get(theme_id), self.detail_bytes.get(theme_id)

    def status(self):
        with self.lock:
            status = dict(self.snapshot.get("status") or {})
            return {
                "ok": self.enabled and self.last_error is None,
                "enabled": self.enabled,
                "state": status.get("state"),
                "theme_clients": self.client_count,
                "cache_version": self.cache_version,
                "cache_age_ms": self.cache_age_ms(),
                "compute_ms": self.last_compute_ms,
                "lock_ms": self.last_lock_ms,
                "lock_wait_ms": self.last_lock_wait_ms,
                "lock_probe_ms": self.last_lock_probe_ms,
                "copy_ms": self.last_copy_ms,
                "lock_busy_skip_count": self.lock_busy_skip_count,
                "payload_bytes": self.last_payload_bytes,
                "last_error": self.last_error,
                "last_updated_at": self.last_updated_at,
                "compute_attempt_count": self.compute_attempt_count,
                "compute_success_count": self.compute_success_count,
                "compute_skip_count": self.compute_skip_count,
                "display_basis": status.get("display_basis"),
                "display_basis_text": status.get("display_basis_text"),
                "hold_until": status.get("hold_until"),
            }

    def run(self):
        while not self.stop_event.is_set():
            with self.lock:
                clients = self.client_count
            if clients <= 0:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            self.refresh()
            delay = self.slow if (self.last_compute_ms or 0) > self.threshold else self.interval
            self.stop_event.wait(delay)


def send_json(handler, payload, status=200):
    if hasattr(handler, "_json"):
        handler._json(payload, status=status)
        return
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def send_bytes(handler, body, content_type, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def stream(handler, service):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    service.register_client()
    last_version = -1
    last_sent_at = 0.0
    try:
        service.refresh(force=service.cache_version <= 0)
        while True:
            now = time.monotonic()
            with service.lock:
                version = service.cache_version
                body = service.snapshot_bytes
            if version != last_version:
                handler.wfile.write(b"event: snapshot\ndata: " + body + b"\n\n")
                handler.wfile.flush()
                last_version = version
                last_sent_at = now
            elif now - last_sent_at >= 2:
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
                last_sent_at = now
            time.sleep(0.1)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        service.unregister_client()


def install(base):
    if getattr(base, "_theme_board_patch_installed", False):
        return

    root = Path(getattr(base, "ROOT", Path(__file__).resolve().parents[1]))
    html_path = root / "docs" / "stockboard_theme_v1.html"
    master_path = root / "config" / "stockboard_theme_master.json"
    persist_path = root / "data" / "runtime" / "stockboard_v2" / "theme_last_close.json"

    original_server_init = base.WebServer.__init__
    original_server_close = base.WebServer.server_close
    original_do_get = base.WebHandler.do_GET

    def patched_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        try:
            service = ThemeCacheService(state, master_path, persist_path=persist_path)
            service.start()
            self.theme_cache_service = service
        except Exception as error:
            self.theme_cache_service = None
            status = getattr(state, "status", None)
            if isinstance(status, dict):
                status["theme_board_last_error"] = f"{type(error).__name__}: {error}"

    def patched_close(self):
        service = getattr(self, "theme_cache_service", None)
        if service:
            service._persist_current(force=True)
            service.stop()
            service.join(timeout=2)
        return original_server_close(self)

    def patched_get(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        service = getattr(self.server, "theme_cache_service", None)

        if parsed.path in {"/theme", "/stockboard_theme_v1.html"}:
            try:
                body = html_path.read_bytes()
            except OSError as error:
                send_json(self, {"error": str(error)}, HTTPStatus.NOT_FOUND)
                return
            send_bytes(self, body, "text/html; charset=utf-8")
            return

        if parsed.path == "/api/v2/themes/status":
            payload = service.status() if service else {"ok": False, "error": "theme service unavailable"}
            send_json(self, payload, 200 if service else HTTPStatus.SERVICE_UNAVAILABLE)
            return

        if parsed.path == "/api/v2/themes/snapshot":
            if not service:
                send_json(self, {"error": "theme service unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            else:
                send_json(self, service.get_snapshot(True)[0])
            return

        if parsed.path == "/api/v2/themes/detail":
            if not service:
                send_json(self, {"error": "theme service unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            theme_id = str((query.get("theme_id") or [""])[0]).strip()
            payload, _body = service.get_detail(theme_id)
            send_json(
                self,
                payload if payload else {"error": "theme not found", "theme_id": theme_id},
                200 if payload else HTTPStatus.NOT_FOUND,
            )
            return

        if parsed.path == "/api/v2/themes/stream":
            if service:
                stream(self, service)
            else:
                send_json(self, {"error": "theme service unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        return original_do_get(self)

    base.WebServer.__init__ = patched_init
    base.WebServer.server_close = patched_close
    base.WebHandler.do_GET = patched_get
    base._theme_board_patch_installed = True
