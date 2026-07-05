from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "data" / "runtime" / "replay_output"
DEFAULT_BOARD_HTML = ROOT / "docs" / "stockboard_v0_3_0_sample.html"

SAFETY_PAYLOAD = {
    "replay_only": True,
    "kiwoom_connection": False,
    "openapi_connection": False,
    "order_enabled": False,
    "live_api_compatible": True,
    "live_api_reuse": False,
    "recommended_api_prefix": "/api",
}


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


def parse_time_part_to_ms(value: str) -> int:
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[1]
    pieces = text.split(":")
    if len(pieces) < 3:
        raise ValueError(f"invalid time: {value}")
    hour = int(pieces[0])
    minute = int(pieces[1])
    second_float = float(pieces[2])
    second = int(second_float)
    milli = int(round((second_float - second) * 1000))
    return (((hour * 60 + minute) * 60 + second) * 1000) + milli


def ms_to_time_part(value: int | float | None) -> str | None:
    if value is None:
        return None
    day = 24 * 60 * 60 * 1000
    ms = int(round(float(value))) % day
    hour = ms // 3_600_000
    ms -= hour * 3_600_000
    minute = ms // 60_000
    ms -= minute * 60_000
    second = ms // 1000
    ms -= second * 1000
    return f"{hour:02d}:{minute:02d}:{second:02d}.{ms:03d}"


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    number = safe_float(value, None)
    if number is None:
        return default
    return int(number)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def related_tick_json_path(events_path: Path, suffix: str) -> Path:
    name = events_path.name.replace("_events.jsonl", suffix)
    return events_path.with_name(name)


def discover_latest_tick_events(out_dir: Path) -> Path:
    paths = sorted(
        out_dir.glob("tick_replay_*_events.jsonl"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not paths:
        raise RuntimeError(f"No tick_replay_*_events.jsonl found in {out_dir}")
    return paths[0]


def load_tick_session(events_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    summary = read_json(related_tick_json_path(events_path, "_summary.json"))
    snapshot = read_json(related_tick_json_path(events_path, "_snapshot.json"))
    if summary is None:
        raise RuntimeError(f"summary not found for {events_path}")
    if snapshot is None:
        raise RuntimeError(f"snapshot not found for {events_path}")

    events: list[dict[str, Any]] = []
    with events_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {events_path}:{line_no}: {exc}") from exc
            if isinstance(event, dict):
                events.append(event)

    if not events:
        raise RuntimeError(f"no events loaded: {events_path}")

    return summary, snapshot, events


def normalize_row(row: dict[str, Any], rank: int | None = None) -> dict[str, Any]:
    price = row.get("price", row.get("current_price", row.get("realtime_price")))
    change_rate = row.get("change_rate", row.get("fluctuation_rate"))
    trade_value = row.get("trade_value_eok", row.get("minute_trade_value_eok_est", row.get("acc_trade_value_eok_est")))
    execution_strength = row.get("execution_strength")
    bid_ask_ratio = row.get("bid_ask_ratio", row.get("bid_ask_ratio_pct"))
    stock_code = str(row.get("stock_code") or row.get("code") or "").zfill(6)
    stock_name = row.get("stock_name") or row.get("name") or ""

    normalized = dict(row)
    normalized.update({
        "rank": rank if rank is not None else row.get("rank"),
        "stock_code": stock_code,
        "code": stock_code,
        "stock_name": stock_name,
        "name": stock_name,
        "price": price,
        "current_price": price,
        "realtime_price": price,
        "display_price": price,
        "change_rate": change_rate,
        "fluctuation_rate": change_rate,
        "trade_value_eok": trade_value,
        "trade_amount_eok": trade_value,
        "value_eok": trade_value,
        "execution_strength": execution_strength,
        "today_strength": execution_strength,
        "one_min_strength": execution_strength,
        "bid_ask_ratio": bid_ask_ratio,
        "balance_ratio": bid_ask_ratio,
        "program_net": row.get("program_net"),
        "foreign_sum": row.get("foreign_sum"),
        "foreign_investor_net": row.get("foreign_investor_net"),
        "trade_metric_7": row.get("trade_metric_7"),
        "last_ts": row.get("last_ts"),
        "last_kind": row.get("last_kind"),
        "replay_only": True,
        "source": "live_compatible_replay",
    })

    normalized["ohlc"] = {
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": price,
        "current": price,
        "vwap": row.get("vwap"),
        "prev_high": row.get("prev_high"),
        "prev_low": row.get("prev_low"),
        "prev_close": row.get("prev_close"),
    }

    return normalized


@dataclass
class ReplayMetrics:
    applied_total: int = 0
    ignored_total: int = 0
    last_apply_ms: float = 0.0
    last_batch_events: int = 0
    api_top100_ms: float = 0.0
    api_patch_ms: float = 0.0
    api_realtime_ms: float = 0.0


class LiveCompatibleReplayDriver:
    def __init__(
        self,
        events_path: Path,
        top_n: int = 100,
        default_speed: float = 1.0,
        start_paused: bool = True,
        start_time: str | None = None,
    ) -> None:
        self.events_path = events_path
        self.summary, self.snapshot, self.events = load_tick_session(events_path)
        self.session_id = str(self.summary.get("session_id") or events_path.stem.replace("_events", ""))
        self.top_n = top_n
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.metrics = ReplayMetrics()

        self.start_text = str(self.summary.get("start") or self.events[0].get("ts"))
        self.start_ms_of_day = parse_time_part_to_ms(self.start_text)
        self.end_text = str(self.summary.get("end") or self.events[-1].get("ts"))

        self.playing = False
        self.speed = float(default_speed)
        self.index = 0
        self.target_rel_ms = 0.0
        self.base_rel_ms = 0.0
        self.base_perf = time.perf_counter()
        self.last_applied_event: dict[str, Any] | None = None
        self.last_api_ts = time.time()

        self.rows_by_code: dict[str, dict[str, Any]] = {}
        for row in self.snapshot.get("rows") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("stock_code") or "").zfill(6)
            if not code.strip("0"):
                continue
            normalized = normalize_row(row, row.get("rank"))
            normalized["last_ts"] = self.snapshot.get("snapshot_ts") or self.start_text
            self.rows_by_code[code] = normalized

        if start_time:
            self.goto_time(start_time)
        else:
            self.reset()

        if not start_paused:
            self.play(self.speed)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run_loop, name="live-compatible-replay-driver", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def reset(self) -> dict[str, Any]:
        with self.lock:
            self.index = 0
            self.target_rel_ms = 0.0
            self.base_rel_ms = 0.0
            self.base_perf = time.perf_counter()
            self.playing = False
            self.last_applied_event = None
            self.metrics = ReplayMetrics()

            self.rows_by_code.clear()
            for row in self.snapshot.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("stock_code") or "").zfill(6)
                if not code.strip("0"):
                    continue
                normalized = normalize_row(row, row.get("rank"))
                normalized["last_ts"] = self.snapshot.get("snapshot_ts") or self.start_text
                self.rows_by_code[code] = normalized

            return self.status_locked()

    def _target_rel_locked(self) -> float:
        if not self.playing:
            return float(self.target_rel_ms)
        elapsed_ms = (time.perf_counter() - self.base_perf) * 1000.0
        return float(self.base_rel_ms) + elapsed_ms * float(self.speed)

    def _sync_target_locked(self) -> None:
        self.target_rel_ms = self._target_rel_locked()

    def play(self, speed: float | None = None) -> dict[str, Any]:
        with self.lock:
            self._sync_target_locked()
            if speed is not None:
                self.speed = max(0.01, float(speed))
            self.base_rel_ms = float(self.target_rel_ms)
            self.base_perf = time.perf_counter()
            self.playing = True
            return self.status_locked()

    def pause(self) -> dict[str, Any]:
        with self.lock:
            self._sync_target_locked()
            self.playing = False
            return self.status_locked()

    def goto_time(self, time_text: str) -> dict[str, Any]:
        target_rel = self.time_to_rel_ms(time_text)
        return self.goto_rel_ms(target_rel)

    def goto_rel_ms(self, rel_ms: float) -> dict[str, Any]:
        with self.lock:
            was_playing = self.playing
            speed = self.speed

            self.reset()
            self.target_rel_ms = max(0.0, float(rel_ms))
            self._apply_due_events_locked(self.target_rel_ms, max_events=2_000_000)
            self.base_rel_ms = float(self.target_rel_ms)
            self.base_perf = time.perf_counter()
            self.playing = was_playing
            self.speed = speed
            return self.status_locked()

    def time_to_rel_ms(self, time_text: str) -> float:
        target = parse_time_part_to_ms(time_text)
        rel = target - self.start_ms_of_day
        if rel < -12 * 3600 * 1000:
            rel += 24 * 3600 * 1000
        return float(max(0, rel))

    def rel_to_time(self, rel_ms: float | int | None) -> str | None:
        if rel_ms is None:
            return None
        return ms_to_time_part(self.start_ms_of_day + float(rel_ms))

    def _run_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    if self.playing:
                        target = self._target_rel_locked()
                        self.target_rel_ms = target
                        self._apply_due_events_locked(target, max_events=50_000)
                        if self.index >= len(self.events):
                            self.playing = False
                time.sleep(0.005)
            except Exception as exc:  # noqa: BLE001
                print(f"[REPLAY_DRIVER_ERROR] {exc}", file=sys.stderr)
                time.sleep(0.1)

    def _apply_due_events_locked(self, target_rel_ms: float, max_events: int) -> None:
        start = time.perf_counter()
        count = 0

        while self.index < len(self.events) and count < max_events:
            event = self.events[self.index]
            rel = safe_float(event.get("rel_ms"), 0.0) or 0.0
            if rel > target_rel_ms:
                break

            self._apply_event_locked(event)
            self.index += 1
            count += 1

        elapsed = (time.perf_counter() - start) * 1000.0
        self.metrics.last_apply_ms = elapsed
        self.metrics.last_batch_events = count
        self.metrics.applied_total += count

    def _apply_event_locked(self, event: dict[str, Any]) -> None:
        code = str(event.get("code") or "").zfill(6)
        if not code.strip("0"):
            self.metrics.ignored_total += 1
            return

        patch = event.get("patch") or {}
        if not isinstance(patch, dict):
            self.metrics.ignored_total += 1
            return

        row = self.rows_by_code.get(code)
        if row is None:
            row = normalize_row({
                "stock_code": code,
                "stock_name": event.get("stock_name"),
                "price": patch.get("price"),
                "change_rate": patch.get("change_rate"),
            })
            self.rows_by_code[code] = row

        row["stock_code"] = code
        row["code"] = code
        row["stock_name"] = patch.get("stock_name") or event.get("stock_name") or row.get("stock_name")
        row["name"] = row.get("stock_name")
        row["price"] = patch.get("price", row.get("price"))
        row["current_price"] = row.get("price")
        row["realtime_price"] = row.get("price")
        row["display_price"] = row.get("price")
        row["change_rate"] = patch.get("change_rate", row.get("change_rate"))
        row["fluctuation_rate"] = row.get("change_rate")
        row["open"] = patch.get("open", row.get("open"))
        row["high"] = patch.get("high", row.get("high"))
        row["low"] = patch.get("low", row.get("low"))
        row["close"] = row.get("price")
        row["acc_volume"] = patch.get("acc_volume", row.get("acc_volume"))
        row["acc_trade_value_eok_est"] = patch.get("acc_trade_value_eok_est", row.get("acc_trade_value_eok_est"))
        row["minute_volume_est"] = patch.get("minute_volume_est", row.get("minute_volume_est"))
        row["minute_trade_value_eok_est"] = patch.get("minute_trade_value_eok_est", row.get("minute_trade_value_eok_est"))
        row["trade_value_eok"] = row.get("minute_trade_value_eok_est") or row.get("trade_value_eok") or row.get("acc_trade_value_eok_est")
        row["trade_amount_eok"] = row.get("trade_value_eok")
        row["value_eok"] = row.get("trade_value_eok")
        row["execution_strength"] = patch.get("execution_strength", row.get("execution_strength"))
        row["today_strength"] = row.get("execution_strength")
        row["one_min_strength"] = row.get("execution_strength")
        row["trade_metric_7"] = patch.get("trade_metric_7", row.get("trade_metric_7"))
        row["ask_remain"] = patch.get("ask_remain", row.get("ask_remain"))
        row["bid_remain"] = patch.get("bid_remain", row.get("bid_remain"))
        row["bid_ask_ratio"] = patch.get("bid_ask_ratio", row.get("bid_ask_ratio"))
        row["bid_ask_ratio_pct"] = patch.get("bid_ask_ratio_pct", row.get("bid_ask_ratio_pct"))
        row["balance_ratio"] = row.get("bid_ask_ratio")
        row["program_net"] = patch.get("program_net", row.get("program_net"))
        row["last_ts"] = event.get("ts")
        row["last_kind"] = event.get("kind")
        row["replay_i"] = event.get("i")
        row["replay_only"] = True
        row["source"] = "live_compatible_replay"
        row["ohlc"] = {
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("price"),
            "current": row.get("price"),
            "vwap": row.get("vwap"),
            "prev_high": row.get("prev_high"),
            "prev_low": row.get("prev_low"),
            "prev_close": row.get("prev_close"),
        }

        self.last_applied_event = event

    def top_rows_locked(self, limit: int | None = None) -> list[dict[str, Any]]:
        rows = [copy.deepcopy(row) for row in self.rows_by_code.values()]
        rows.sort(
            key=lambda row: (
                safe_float(row.get("trade_value_eok"), 0.0) or 0.0,
                safe_float(row.get("acc_trade_value_eok_est"), 0.0) or 0.0,
                safe_float(row.get("minute_trade_value_eok_est"), 0.0) or 0.0,
            ),
            reverse=True,
        )
        n = limit or self.top_n
        out = []
        for rank, row in enumerate(rows[:n], start=1):
            out.append(normalize_row(row, rank))
        return out

    def top_rows(self, limit: int | None = None) -> list[dict[str, Any]]:
        started = time.perf_counter()
        with self.lock:
            rows = self.top_rows_locked(limit)
            self.metrics.api_top100_ms = (time.perf_counter() - started) * 1000.0
            self.last_api_ts = time.time()
            return rows

    def rows_by_codes(self, codes: list[str]) -> list[dict[str, Any]]:
        started = time.perf_counter()
        with self.lock:
            rows = []
            for code in codes:
                clean = str(code).strip().upper()
                if clean.startswith("A"):
                    clean = clean[1:]
                if "_" in clean:
                    clean = clean.split("_", 1)[0]
                clean = clean.zfill(6)
                row = self.rows_by_code.get(clean)
                if row:
                    rows.append(normalize_row(copy.deepcopy(row)))
            self.metrics.api_realtime_ms = (time.perf_counter() - started) * 1000.0
            self.last_api_ts = time.time()
            return rows

    def patch_payload(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self.lock:
            rows = self.top_rows_locked(self.top_n)
            by_code = {row["stock_code"]: row for row in rows}
            status = self.status_locked()
            sequence = int(status.get("processed_events") or self.index or 0)
            self.metrics.api_patch_ms = (time.perf_counter() - started) * 1000.0
            self.last_api_ts = time.time()
            return {
                "ok": True,
                "replay_only": True,
                "ts": time.time(),
                "sequence": sequence,
                "target_time": self.rel_to_time(self.target_rel_ms),
                "last_applied_tick": self.last_applied_time_locked(),
                "rows": rows,
                "data": rows,
                "patches": rows,
                "patch_by_code": by_code,
                "rowByCode": by_code,
                "by_code": by_code,
                "count": len(rows),
                "status": status,
                "safety": SAFETY_PAYLOAD,
            }

    def last_applied_time_locked(self) -> str | None:
        if self.last_applied_event:
            return self.last_applied_event.get("time_text") or self.last_applied_event.get("ts")
        return None

    def next_event_locked(self) -> dict[str, Any] | None:
        if self.index >= len(self.events):
            return None
        return self.events[self.index]

    def status_locked(self) -> dict[str, Any]:
        target = self._target_rel_locked() if self.playing else self.target_rel_ms
        next_event = self.next_event_locked()
        next_rel = safe_float((next_event or {}).get("rel_ms"), None)
        last_rel = safe_float((self.last_applied_event or {}).get("rel_ms"), None)

        processing_lag_ms = 0.0
        if next_rel is not None and next_rel <= target:
            processing_lag_ms = max(0.0, target - next_rel)

        event_gap_ms = 0.0
        if last_rel is not None and target >= last_rel:
            if next_rel is None or next_rel > target:
                event_gap_ms = max(0.0, target - last_rel)

        return {
            "ok": True,
            "mode": "live_compatible_replay",
            "session_id": self.session_id,
            "playing": self.playing,
            "speed": self.speed,
            "target_rel_ms": round(float(target), 3),
            "target_time": self.rel_to_time(target),
            "last_applied_tick": self.last_applied_time_locked(),
            "last_applied_i": (self.last_applied_event or {}).get("i"),
            "next_tick": (next_event or {}).get("time_text") or (next_event or {}).get("ts"),
            "next_i": (next_event or {}).get("i"),
            "processing_lag_ms": round(processing_lag_ms, 3),
            "event_gap_ms": round(event_gap_ms, 3),
            "processed_events": self.index,
            "total_events": len(self.events),
            "rows": len(self.rows_by_code),
            "top_n": self.top_n,
            "start": self.start_text,
            "end": self.end_text,
            "first_event_ts": self.events[0].get("ts"),
            "last_event_ts": self.events[-1].get("ts"),
            "events_per_second_max": self.summary.get("events_per_second_max"),
            "events_per_second_avg": self.summary.get("events_per_second_avg"),
            "last_batch_events": self.metrics.last_batch_events,
            "last_apply_ms": round(self.metrics.last_apply_ms, 3),
            "applied_total": self.metrics.applied_total,
            "ignored_total": self.metrics.ignored_total,
            "api_top100_ms": round(self.metrics.api_top100_ms, 3),
            "api_patch_ms": round(self.metrics.api_patch_ms, 3),
            "api_realtime_ms": round(self.metrics.api_realtime_ms, 3),
            "safety": SAFETY_PAYLOAD,
        }

    def status(self) -> dict[str, Any]:
        with self.lock:
            return self.status_locked()


class ReplayHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        driver: LiveCompatibleReplayDriver,
        board_html: Path,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.driver = driver
        self.board_html = board_html


class LiveCompatibleReplayHandler(BaseHTTPRequestHandler):
    server_version = "StockBoardLiveCompatibleReplay/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    def send_bytes(self, data: bytes, status: int = 200, content_type: str = "application/octet-stream") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: Any, status: int = 200) -> None:
        self.send_bytes(write_json_bytes(payload), status=status, content_type="application/json; charset=utf-8")

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"ok": False, "error": message, "status": status, "safety": SAFETY_PAYLOAD}, status=status)

    def do_OPTIONS(self) -> None:
        self.send_bytes(b"", status=204, content_type="text/plain")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path in {"/", "/index.html", "/stockboard", "/stockboard.html"}:
                self.handle_board()
            elif path.startswith("/docs/"):
                self.handle_static_doc(path)
            elif path.startswith("/assets/"):
                self.handle_static_asset(path)
            elif path == "/api/health":
                self.handle_health()
            elif path == "/api/market_supply":
                self.handle_market_supply()
            elif path == "/api/us_market":
                self.handle_us_market()
            elif path == "/api/hot_lane_status":
                self.handle_hot_lane_status()
            elif path == "/api/hot_selected":
                self.handle_hot_selected(query)
            elif path == "/api/price_light_patch":
                self.handle_realtime_patch(query)
            elif path == "/api/close_metrics_request":
                self.handle_close_metrics_request(query)
            elif path == "/api/close_metrics_status":
                self.handle_close_metrics_status()
            elif path == "/api/aftermarket_metrics_backfill_status":
                self.handle_aftermarket_backfill_status()
            elif path == "/api/aftermarket_metrics_backfill_start":
                self.handle_aftermarket_backfill_start()
            elif path == "/api/aftermarket_metrics_backfill_cancel":
                self.handle_aftermarket_backfill_cancel()
            elif path == "/api/top100_filter_report":
                self.handle_top100_filter_report()
            elif path == "/api/rank_mode_status":
                self.handle_rank_mode_status()
            elif path in {"/api/candidate_safe_ranking", "/api/candidate_preview"}:
                self.handle_candidate_preview()
            elif path == "/api/opt10055_probe":
                self.handle_generic_disabled_probe()
            elif path == "/api/top100":
                self.handle_top100(query)
            elif path == "/api/realtime_patch":
                self.handle_realtime_patch(query)
            elif path == "/api/hot_realtime_patch":
                self.handle_realtime_patch(query)
            elif path == "/api/realtime":
                self.handle_realtime(query)
            elif path == "/api/realtime_status":
                self.handle_status()
            elif path == "/api/realtime_provider_status":
                self.handle_provider_status()
            elif path == "/api/replay_driver/status":
                self.handle_status()
            elif path == "/api/replay_driver/play":
                self.handle_play(query)
            elif path == "/api/replay_driver/pause":
                self.handle_pause()
            elif path == "/api/replay_driver/goto":
                self.handle_goto(query)
            elif path == "/api/replay_driver/reset":
                self.handle_reset()
            elif path == "/api/replay_driver/health":
                self.handle_health()
            elif path.startswith("/api/order") or path.startswith("/api/buy") or path.startswith("/api/sell"):
                self.send_error_json(HTTPStatus.FORBIDDEN, "Order endpoint disabled in replay mode")
            elif path == "/favicon.ico":
                self.send_bytes(b"", status=204, content_type="image/x-icon")
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, f"Unknown live-compatible replay endpoint: {path}")
        except Exception as exc:  # noqa: BLE001
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def handle_board(self) -> None:
        path = self.server.board_html
        if not path.exists():
            html = f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<body>
<h1>StockBoard Live-Compatible Replay Server</h1>
<p>Board HTML not found: {path}</p>
<p>API status: <a href="/api/replay_driver/status">/api/replay_driver/status</a></p>
</body></html>"""
            self.send_bytes(html.encode("utf-8"), content_type="text/html; charset=utf-8")
            return
        self.send_bytes(path.read_bytes(), content_type="text/html; charset=utf-8")


    def handle_static_asset(self, path: str) -> None:
        rel = unquote(path.lstrip("/")).replace("\\", "/")
        target = (ROOT / "docs" / rel).resolve()
        assets_root = (ROOT / "docs" / "assets").resolve()
        if not str(target).startswith(str(assets_root)) or not target.exists() or not target.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, f"Asset not found: {path}")
            return

        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix.lower() in {".js"}:
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix.lower() in {".css"}:
            content_type = "text/css; charset=utf-8"
        elif target.suffix.lower() in {".html", ".htm"}:
            content_type = "text/html; charset=utf-8"

        self.send_bytes(target.read_bytes(), content_type=content_type)

    def handle_static_doc(self, path: str) -> None:
        rel = unquote(path.lstrip("/")).replace("\\", "/")
        target = (ROOT / rel).resolve()
        docs_root = (ROOT / "docs").resolve()
        if not str(target).startswith(str(docs_root)) or not target.exists() or not target.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, f"Static file not found: {path}")
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix.lower() in {".html", ".htm"}:
            content_type = "text/html; charset=utf-8"
        elif target.suffix.lower() in {".js"}:
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix.lower() in {".css"}:
            content_type = "text/css; charset=utf-8"
        self.send_bytes(target.read_bytes(), content_type=content_type)


    def handle_market_supply(self) -> None:
        # Keep this payload intentionally conservative.
        # The live board validates market_session before rendering.
        payload = {
            "ok": True,
            "replay_only": True,
            "market_session": "regular",
            "session": "regular",
            "session_label": "장중",
            "updated_at": time.time(),
            "kospi": {
                "market": "KOSPI",
                "index": None,
                "change_rate": None,
                "up": None,
                "down": None,
                "foreign_eok": None,
                "individual_eok": None,
                "institution_eok": None,
                "program_eok": None,
                "foreign_future_eok": None,
            },
            "kosdaq": {
                "market": "KOSDAQ",
                "index": None,
                "change_rate": None,
                "up": None,
                "down": None,
                "foreign_eok": None,
                "individual_eok": None,
                "institution_eok": None,
                "program_eok": None,
                "foreign_future_eok": None,
            },
            "markets": [
                {"market": "KOSPI", "index": None, "change_rate": None, "up": None, "down": None, "foreign_eok": None, "individual_eok": None, "institution_eok": None, "program_eok": None, "foreign_future_eok": None},
                {"market": "KOSDAQ", "index": None, "change_rate": None, "up": None, "down": None, "foreign_eok": None, "individual_eok": None, "institution_eok": None, "program_eok": None, "foreign_future_eok": None},
            ],
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_us_market(self) -> None:
        payload = {
            "ok": True,
            "replay_only": True,
            "items": [
                {"symbol": "NASDAQ", "name": "나스닥선물", "price": None, "change_rate": None},
                {"symbol": "QQQ", "name": "QQQ", "price": None, "change_rate": None},
                {"symbol": "SOXL", "name": "SOXL", "price": None, "change_rate": None},
                {"symbol": "SMH", "name": "SMH", "price": None, "change_rate": None},
                {"symbol": "IBB", "name": "IBB", "price": None, "change_rate": None},
                {"symbol": "LIT", "name": "LIT", "price": None, "change_rate": None},
                {"symbol": "BOTZ", "name": "BOTZ", "price": None, "change_rate": None},
            ],
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_hot_lane_status(self) -> None:
        status = self.server.driver.status()
        payload = {
            "ok": True,
            "replay_only": True,
            "adaptive_speed": {
                "mode": "replay",
                "hot_interval_ms": 500,
                "mid_interval_ms": 2000,
                "pool_interval_ms": 5000,
            },
            "hot_lane": {"ok": True, "source": "replay", "status": status},
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_hot_selected(self, query: dict[str, list[str]]) -> None:
        selected = first_query(query, "selected") or first_query(query, "code") or ""
        rows = self.server.driver.rows_by_codes([selected]) if selected else []
        row = rows[0] if rows else None
        payload = {
            "ok": True,
            "replay_only": True,
            "selected": selected,
            "row": row,
            "rows": rows,
            "data": rows,
            "patches": rows,
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_close_metrics_request(self, query: dict[str, list[str]]) -> None:
        codes_text = first_query(query, "codes") or first_query(query, "code") or ""
        codes = [item.strip() for item in codes_text.replace(";", ",").split(",") if item.strip()]
        rows = self.server.driver.rows_by_codes(codes)
        payload = {
            "ok": True,
            "replay_only": True,
            "completed": True,
            "requested": len(codes),
            "rows": rows,
            "data": rows,
            "metrics_by_code": {row["stock_code"]: row for row in rows},
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_close_metrics_status(self) -> None:
        self.send_json({
            "ok": True,
            "replay_only": True,
            "pending": 0,
            "completed": True,
            "safety": SAFETY_PAYLOAD,
        })

    def handle_aftermarket_backfill_status(self) -> None:
        self.send_json({
            "ok": True,
            "replay_only": True,
            "running": False,
            "completed": True,
            "trading_date": "2026-06-16",
            "safety": SAFETY_PAYLOAD,
        })

    def handle_aftermarket_backfill_start(self) -> None:
        self.handle_aftermarket_backfill_status()

    def handle_aftermarket_backfill_cancel(self) -> None:
        self.handle_aftermarket_backfill_status()

    def handle_top100_filter_report(self) -> None:
        self.send_json({
            "ok": True,
            "replay_only": True,
            "rows": [],
            "data": [],
            "safety": SAFETY_PAYLOAD,
        })

    def handle_rank_mode_status(self) -> None:
        self.send_json({
            "ok": True,
            "replay_only": True,
            "rank_mode": "auto",
            "available": ["auto", "previous", "today"],
            "safety": SAFETY_PAYLOAD,
        })

    def handle_candidate_preview(self) -> None:
        rows = self.server.driver.top_rows(20)
        self.send_json({
            "ok": True,
            "replay_only": True,
            "rows": rows,
            "data": rows,
            "safety": SAFETY_PAYLOAD,
        })

    def handle_generic_disabled_probe(self) -> None:
        self.send_json({
            "ok": True,
            "replay_only": True,
            "enabled": False,
            "message": "disabled in live-compatible replay mode",
            "safety": SAFETY_PAYLOAD,
        })

    def handle_top100(self, query: dict[str, list[str]]) -> None:
        limit = parse_int(first_query(query, "limit"), self.server.driver.top_n)
        rows = self.server.driver.top_rows(limit)
        # Existing StockBoard code historically expects a raw array.
        self.send_json(rows)

    def handle_realtime_patch(self, query: dict[str, list[str]]) -> None:
        self.send_json(self.server.driver.patch_payload())

    def handle_realtime(self, query: dict[str, list[str]]) -> None:
        codes_text = first_query(query, "codes") or first_query(query, "code") or ""
        codes = [item.strip() for item in codes_text.replace(";", ",").split(",") if item.strip()]
        rows = self.server.driver.rows_by_codes(codes)
        by_code = {row["stock_code"]: row for row in rows}
        payload = {
            "ok": True,
            "rows": rows,
            "data": rows,
            "by_code": by_code,
            "rowByCode": by_code,
            "patches": by_code,
            "count": len(rows),
            "status": self.server.driver.status(),
            "safety": SAFETY_PAYLOAD,
        }
        # Also expose each code at top-level for object-style consumers.
        payload.update(by_code)
        self.send_json(payload)

    def handle_status(self) -> None:
        self.send_json(self.server.driver.status())

    def handle_provider_status(self) -> None:
        status = self.server.driver.status()
        payload = {
            "ok": True,
            "provider": "live_compatible_replay",
            "realtime_provider": "live_compatible_replay",
            "connected": True,
            "login": False,
            "kiwoom_connected": False,
            "openapi_connected": False,
            "replay_only": True,
            "requested": False,
            "status": status,
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_health(self) -> None:
        payload = {
            "ok": True,
            "server": "StockBoard Live-Compatible Replay Server",
            "driver": self.server.driver.status(),
            "safety": SAFETY_PAYLOAD,
        }
        self.send_json(payload)

    def handle_play(self, query: dict[str, list[str]]) -> None:
        speed = parse_float(first_query(query, "speed"), None)
        self.send_json(self.server.driver.play(speed))

    def handle_pause(self) -> None:
        self.send_json(self.server.driver.pause())

    def handle_goto(self, query: dict[str, list[str]]) -> None:
        time_text = first_query(query, "time") or first_query(query, "t")
        if not time_text:
            self.send_error_json(HTTPStatus.BAD_REQUEST, "missing time parameter, e.g. ?time=09:00:00")
            return
        status = self.server.driver.goto_time(time_text)
        play_text = first_query(query, "play")
        if play_text in {"1", "true", "yes", "on"}:
            speed = parse_float(first_query(query, "speed"), None)
            status = self.server.driver.play(speed)
        self.send_json(status)

    def handle_reset(self) -> None:
        self.send_json(self.server.driver.reset())


def first_query(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0]
    return value if value != "" else None


def parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def parse_float(value: str | None, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAPI-free live-compatible replay server for StockBoard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18010)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--events", default=None)
    parser.add_argument("--board-html", default=str(DEFAULT_BOARD_HTML))
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--start-paused", action="store_true", default=True)
    parser.add_argument("--autoplay", action="store_true")
    parser.add_argument("--start-time", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    events_path = Path(args.events) if args.events else discover_latest_tick_events(out_dir)
    board_html = Path(args.board_html)

    driver = LiveCompatibleReplayDriver(
        events_path=events_path,
        top_n=args.top_n,
        default_speed=args.speed,
        start_paused=not args.autoplay,
        start_time=args.start_time,
    )
    driver.start()

    server = ReplayHTTPServer(
        (args.host, args.port),
        LiveCompatibleReplayHandler,
        driver=driver,
        board_html=board_html,
    )

    print("STOCKBOARD_LIVE_COMPATIBLE_REPLAY_SERVER_START")
    print(f"host: {args.host}")
    print(f"port: {args.port}")
    print(f"events_path: {events_path}")
    print(f"board_html: {board_html}")
    print(f"session_id: {driver.session_id}")
    print(f"event_count: {len(driver.events):,}")
    print(f"start: {driver.start_text}")
    print(f"end: {driver.end_text}")
    print("safety:", SAFETY_PAYLOAD)
    print(f"board: http://{args.host}:{args.port}/")
    print(f"status: http://{args.host}:{args.port}/api/replay_driver/status")
    print(f"top100: http://{args.host}:{args.port}/api/top100")
    print(f"patch: http://{args.host}:{args.port}/api/realtime_patch")
    print(f"goto0900: http://{args.host}:{args.port}/api/replay_driver/goto?time=09:00:00")
    print(f"play1x: http://{args.host}:{args.port}/api/replay_driver/play?speed=1")
    sys.stdout.flush()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("STOCKBOARD_LIVE_COMPATIBLE_REPLAY_SERVER_STOP")
    finally:
        driver.close()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())