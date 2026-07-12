from __future__ import annotations

import json
import os
import struct
import threading
import time
from datetime import datetime
from typing import Any


ACTIVE_PHASES = {"premarket", "opening_call", "regular", "closing_call", "after_wait", "aftermarket"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _iso_age_ms(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
    except ValueError:
        return None
    return max(0, round((datetime.now() - parsed).total_seconds() * 1000))


def _tone_for(value: float | None, warn: float, bad: float, *, inverse: bool = False) -> str:
    if value is None:
        return "muted"
    if inverse:
        return "good" if value >= warn else "warn" if value >= bad else "bad"
    return "good" if value <= warn else "warn" if value <= bad else "bad"


class BoardPerformanceService(threading.Thread):
    """Sample cheap platform counters once per second and serve cached diagnostics."""

    def __init__(self, server: Any, *, interval_sec: float = 1.0) -> None:
        super().__init__(name="stockboard-v2-board-performance", daemon=True)
        self.server = server
        self.state = server.state
        self.interval_sec = max(0.5, float(interval_sec))
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.cache_version = 0
        self.payload: dict[str, Any] = {}
        self.payload_bytes = b""
        self.last_updated_at: str | None = None
        self.last_error: str | None = None
        self.last_cpu_process = time.process_time()
        self.last_cpu_wall = time.monotonic()
        self.last_drop_total = 0
        self.process_cpu_pct = 0.0
        self.sample()

    @staticmethod
    def _now_text() -> str:
        return datetime.now().isoformat(timespec="milliseconds")

    def _state_status(self) -> dict[str, Any]:
        lock = getattr(self.state, "lock", None)
        acquired = False
        if lock is not None:
            try:
                acquired = lock.acquire(blocking=False)
            except TypeError:
                acquired = lock.acquire(False)
            if not acquired:
                return {}
        try:
            return dict(getattr(self.state, "status", {}) or {})
        finally:
            if lock is not None and acquired:
                lock.release()

    def _cpu(self) -> float:
        process_now = time.process_time()
        wall_now = time.monotonic()
        process_delta = max(0.0, process_now - self.last_cpu_process)
        wall_delta = max(0.001, wall_now - self.last_cpu_wall)
        self.last_cpu_process = process_now
        self.last_cpu_wall = wall_now
        logical = max(1, int(os.cpu_count() or 1))
        self.process_cpu_pct = round(
            min(999.0, process_delta / wall_delta * 100.0 / logical), 1
        )
        return self.process_cpu_pct

    @staticmethod
    def _service_status(server: Any, name: str) -> dict[str, Any]:
        service = getattr(server, name, None)
        if service is None or not hasattr(service, "status"):
            return {}
        try:
            result = service.status()
            return result if isinstance(result, dict) else {}
        except Exception as error:
            return {"state": "ERROR", "last_error": f"{type(error).__name__}: {error}"}

    @staticmethod
    def _sender_status(collector: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(collector, dict):
            return {}
        sender = collector.get("sender_stats")
        if isinstance(sender, dict):
            return sender
        status = collector.get("status")
        if isinstance(status, dict) and isinstance(status.get("sender_stats"), dict):
            return status["sender_stats"]
        return {}

    @staticmethod
    def _metric(key: str, label: str, text: str, tone: str) -> dict[str, str]:
        return {"key": key, "label": label, "text": text, "tone": tone}

    def _bottleneck(
        self,
        *,
        worker_bits: int,
        event_age_ms: int | None,
        phase: str,
        pending: int,
        drop_delta: int,
        cpu_pct: float,
        stock_compute: float,
        stock_serialize: float,
        theme_compute: float,
        theme_lock: float,
    ) -> tuple[str, str, str]:
        if worker_bits != 64:
            return "RED", "WORKER_32BIT", "worker가 32비트 Python으로 실행됨"
        if drop_delta > 0:
            return "RED", "DROP", f"최근 표본에서 {drop_delta}건 drop 증가"
        if pending > 500:
            return "RED", "COLLECTOR_QUEUE", f"collector pending {pending}"
        if phase in ACTIVE_PHASES and event_age_ms is not None and event_age_ms > 3000:
            return "RED", "STALE", f"마지막 이벤트 {event_age_ms}ms 지연"
        if cpu_pct > 85:
            return "RED", "WORKER_CPU", f"worker CPU {cpu_pct:.1f}%"
        if stock_compute > 100:
            return "RED", "STOCK_COMPUTE", f"StockBoard 계산 {stock_compute:.1f}ms"
        if stock_compute > 50:
            return "YELLOW", "STOCK_COMPUTE", f"StockBoard 계산 {stock_compute:.1f}ms"
        if stock_serialize > 20:
            return "YELLOW", "SERIALIZE", f"직렬화 {stock_serialize:.1f}ms"
        if theme_compute > 30 or theme_lock > 2:
            return "YELLOW", "THEME", f"Theme compute {theme_compute:.1f}ms / copy {theme_lock:.1f}ms"
        if pending > 50:
            return "YELLOW", "COLLECTOR_QUEUE", f"collector pending {pending}"
        return "GREEN", "NORMAL", "공통 속도 지표 정상"

    def sample(self) -> dict[str, Any]:
        try:
            status = self._state_status()
            collector = (
                status.get("collector_status")
                if isinstance(status.get("collector_status"), dict)
                else {}
            )
            sender = self._sender_status(collector)
            stock = self._service_status(self.server, "stockboard_snapshot_cache")
            theme = self._service_status(self.server, "theme_cache_service")

            phase = str(status.get("market_phase") or "")
            event_age_ms = _iso_age_ms(status.get("last_event_at"))
            sent_per_sec = _number(sender.get("sent_per_sec"))
            pending = _integer(
                sender.get("pending_total_count")
                or sender.get("pending_count")
                or sender.get("queue_size")
            )
            collector_drop = sum(
                _integer(sender.get(key))
                for key in (
                    "dropped_count",
                    "drop_count",
                    "queue_drop_count",
                    "pending_drop_count",
                    "coalesce_drop_count",
                )
            )
            logger_drop = _integer(status.get("event_log_dropped_count"))
            drop_total = collector_drop + logger_drop
            drop_delta = max(0, drop_total - self.last_drop_total)
            self.last_drop_total = drop_total

            event_log_queue = _integer(status.get("event_log_queue_size"))
            worker_bits = struct.calcsize("P") * 8
            cpu_pct = self._cpu()
            stock_compute = _number(stock.get("compute_ms"))
            stock_serialize = _number(stock.get("serialize_ms"))
            theme_compute = _number(theme.get("compute_ms"))
            theme_copy = _number(theme.get("copy_ms") or theme.get("lock_ms"))
            level, bottleneck, bottleneck_text = self._bottleneck(
                worker_bits=worker_bits,
                event_age_ms=event_age_ms,
                phase=phase,
                pending=pending,
                drop_delta=drop_delta,
                cpu_pct=cpu_pct,
                stock_compute=stock_compute,
                stock_serialize=stock_serialize,
                theme_compute=theme_compute,
                theme_lock=theme_copy,
            )

            active = phase in ACTIVE_PHASES
            freshness_text = (
                f"{event_age_ms}ms"
                if event_age_ms is not None and active
                else "장마감"
                if not active
                else "-"
            )
            freshness_tone = (
                _tone_for(
                    float(event_age_ms) if event_age_ms is not None else None,
                    500,
                    3000,
                )
                if active
                else "muted"
            )
            recv_tone = "good" if sent_per_sec > 0 else ("muted" if not active else "warn")
            common_metrics = [
                self._metric("bottleneck", "병목", bottleneck_text, level.lower()),
                self._metric("freshness", "E2E", freshness_text, freshness_tone),
                self._metric("recv", "수신", f"{sent_per_sec:.0f}/s", recv_tone),
                self._metric("pending", "대기", str(pending), _tone_for(float(pending), 50, 500)),
                self._metric(
                    "drops",
                    "드롭",
                    f"+{drop_delta} / {drop_total}",
                    "bad" if drop_delta > 0 else "good",
                ),
                self._metric(
                    "worker_queue",
                    "WorkerQ",
                    str(event_log_queue),
                    _tone_for(float(event_log_queue), 1000, 10000),
                ),
                self._metric("cpu", "CPU", f"{cpu_pct:.1f}%", _tone_for(cpu_pct, 60, 85)),
                self._metric(
                    "bits",
                    "Worker",
                    f"{worker_bits}bit",
                    "good" if worker_bits == 64 else "bad",
                ),
            ]

            board_metrics = {
                "stockboard": [
                    self._metric(
                        "compute",
                        "계산",
                        f"{stock_compute:.1f}ms",
                        _tone_for(stock_compute, 50, 100),
                    ),
                    self._metric(
                        "serialize",
                        "직렬",
                        f"{stock_serialize:.1f}ms",
                        _tone_for(stock_serialize, 10, 20),
                    ),
                    self._metric(
                        "cache_age",
                        "캐시",
                        f"{stock.get('cache_age_ms')}ms"
                        if stock.get("cache_age_ms") is not None
                        else "-",
                        _tone_for(_number(stock.get("cache_age_ms"), 999999), 300, 1000),
                    ),
                    self._metric("clients", "접속", str(_integer(stock.get("clients"))), "muted"),
                    self._metric(
                        "payload",
                        "Payload",
                        f"{_number(stock.get('payload_bytes')) / 1024:.1f}KB",
                        _tone_for(_number(stock.get("payload_bytes")) / 1024, 500, 1000),
                    ),
                ],
                "themeboard": [
                    self._metric(
                        "compute",
                        "계산",
                        f"{theme_compute:.1f}ms",
                        _tone_for(theme_compute, 10, 30),
                    ),
                    self._metric(
                        "copy",
                        "복사",
                        f"{theme_copy:.2f}ms",
                        _tone_for(theme_copy, 1, 2),
                    ),
                    self._metric(
                        "cache_age",
                        "캐시",
                        f"{theme.get('cache_age_ms')}ms"
                        if theme.get("cache_age_ms") is not None
                        else "-",
                        _tone_for(_number(theme.get("cache_age_ms"), 999999), 1500, 5000),
                    ),
                    self._metric(
                        "clients",
                        "접속",
                        str(_integer(theme.get("theme_clients"))),
                        "muted",
                    ),
                    self._metric(
                        "payload",
                        "Payload",
                        f"{_number(theme.get('payload_bytes')) / 1024:.1f}KB",
                        _tone_for(_number(theme.get("payload_bytes")) / 1024, 100, 300),
                    ),
                ],
                "strategyboard": [self._metric("state", "상태", "준비중", "muted")],
                "boards": [self._metric("state", "상태", level, level.lower())],
            }

            next_version = self.cache_version + 1
            payload = {
                "schema_version": 1,
                "source": "board_platform_performance",
                "ts": self._now_text(),
                "cache_version": next_version,
                "overall_state": level,
                "bottleneck": bottleneck,
                "bottleneck_text": bottleneck_text,
                "market_phase": phase,
                "common": {
                    "event_age_ms": event_age_ms,
                    "collector_sent_per_sec": sent_per_sec,
                    "collector_pending": pending,
                    "drop_total": drop_total,
                    "drop_delta": drop_delta,
                    "event_log_queue_size": event_log_queue,
                    "worker_cpu_pct": cpu_pct,
                    "worker_bits": worker_bits,
                    "stream_clients": _integer(status.get("stream_clients")),
                    "tcp_clients": _integer(status.get("tcp_clients")),
                },
                "boards": {
                    "stockboard": stock,
                    "themeboard": theme,
                    "strategyboard": {"state": "DISABLED"},
                },
                "display_common": common_metrics,
                "display_boards": board_metrics,
            }
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            with self.lock:
                self.cache_version = next_version
                self.payload = payload
                self.payload_bytes = body
                self.last_updated_at = payload["ts"]
                self.last_error = None
            return payload
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            return {}

    def get_payload(self, board_id: str = "") -> dict[str, Any]:
        with self.lock:
            payload = dict(self.payload)
            payload["display_metrics"] = [
                *(self.payload.get("display_common") or []),
                *((self.payload.get("display_boards") or {}).get(board_id) or []),
            ]
            return payload

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": self.last_error is None,
                "overall_state": self.payload.get("overall_state") or "WAIT",
                "cache_version": self.cache_version,
                "last_updated_at": self.last_updated_at,
                "last_error": self.last_error,
            }

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval_sec):
            self.sample()
