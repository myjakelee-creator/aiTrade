from __future__ import annotations

import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from stockboard_theme_engine import ThemeBoardEngine
from stockboard_theme_master import DEFAULT_THEME_MASTER_PATH, load_theme_master

_THEME_ROW_KEYS = (
    "stock_code",
    "stock_name",
    "trade_value_eok",
    "price",
    "change_rate",
    "execution_strength",
    "strength_5m",
    "program_net",
    "large_trade_net_sum_eok",
    "large_trade_net_count",
    "ohlc",
)


class ThemeBoardCacheService:
    def __init__(
        self,
        state: Any,
        *,
        master_path: str | Path | None = None,
        interval_sec: float = 1.0,
        autostart: bool = True,
    ) -> None:
        self.state = state
        self.master_path = Path(master_path) if master_path is not None else DEFAULT_THEME_MASTER_PATH
        self.interval_sec = max(1.0, float(interval_sec))
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.input_version = 0
        self.cache_version = 0
        self.last_input_signature: tuple[Any, ...] | None = None
        self.cache: dict[str, Any] = {
            "schema_version": 1,
            "source": "stockboard_theme_cache",
            "version": 0,
            "updated_at": None,
            "theme_count": 0,
            "themes": [],
            "status": {"ready": False, "last_error": None},
        }
        # Keep the full theme master.  The worker universe is only the current
        # Top300 pool, so using it as a validator would remove off-pool members
        # and falsely inflate theme coverage.
        self.master = load_theme_master(self.master_path)
        self.engine = ThemeBoardEngine(self.master)
        self.metrics = {
            "theme_calculate_count": 0,
            "theme_cache_hit_count": 0,
            "theme_calculate_last_ms": None,
            "theme_calculate_max_ms": 0.0,
            "theme_last_error": None,
            "theme_master_summary": self.master.summary(),
        }
        self._publish_status()
        if autostart:
            self.start()

    def start(self) -> None:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(
                target=self._run,
                name="stockboard-theme-cache",
                daemon=True,
            )
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.refresh_now(force=self.cache_version == 0)
            if self.stop_event.wait(self.interval_sec):
                break

    def _copy_state_input(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        state_lock = getattr(self.state, "lock", None)

        def copy_now() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            names = dict(getattr(self.state, "name_by_code", {}) or {})
            status = dict(getattr(self.state, "status", {}) or {})
            copied_rows: list[dict[str, Any]] = []
            for code, raw in (getattr(self.state, "quotes", {}) or {}).items():
                if not isinstance(raw, dict):
                    continue
                row = {key: raw.get(key) for key in _THEME_ROW_KEYS}
                row["stock_code"] = str(row.get("stock_code") or code)
                row["stock_name"] = row.get("stock_name") or names.get(code) or code
                if isinstance(row.get("ohlc"), dict):
                    row["ohlc"] = dict(row["ohlc"])
                copied_rows.append(row)
            return copied_rows, status

        if state_lock is None:
            return copy_now()
        with state_lock:
            return copy_now()

    def _snapshot_rows(self) -> tuple[list[dict[str, Any]], str, tuple[Any, ...]]:
        rows, status = self._copy_state_input()
        signature_parts = [
            (
                row["stock_code"],
                row.get("trade_value_eok"),
                row.get("price"),
                row.get("change_rate"),
                row.get("execution_strength"),
                row.get("strength_5m"),
                row.get("program_net"),
                row.get("large_trade_net_sum_eok"),
            )
            for row in rows
        ]
        signature_parts.sort(key=lambda item: item[0])
        trading_date = str(
            status.get("market_trading_date")
            or status.get("active_trading_date")
            or status.get("trading_date")
            or ""
        )
        signature = (trading_date, tuple(signature_parts))
        return rows, trading_date, signature

    def refresh_now(self, *, force: bool = False) -> dict[str, Any]:
        try:
            rows, trading_date, signature = self._snapshot_rows()
            with self.lock:
                if not force and signature == self.last_input_signature:
                    self.metrics["theme_cache_hit_count"] += 1
                    self._publish_status()
                    return self._payload_unlocked(include_details=False)
                self.last_input_signature = signature
                self.input_version += 1

            result = self.engine.update(rows, trading_date=trading_date)
            calculate_ms = float(result.get("calculate_ms") or 0.0)
            with self.lock:
                self.cache_version += 1
                self.metrics["theme_calculate_count"] += 1
                self.metrics["theme_calculate_last_ms"] = round(calculate_ms, 3)
                self.metrics["theme_calculate_max_ms"] = round(
                    max(float(self.metrics.get("theme_calculate_max_ms") or 0.0), calculate_ms),
                    3,
                )
                self.metrics["theme_last_error"] = None
                self.cache = {
                    "schema_version": 1,
                    "source": "stockboard_theme_cache",
                    "version": self.cache_version,
                    "input_version": self.input_version,
                    "updated_at": time.time(),
                    "trading_date": trading_date,
                    "theme_count": result.get("theme_count", 0),
                    "themes": result.get("themes", []),
                    "details": result.get("details", {}),
                    "status": {
                        "ready": True,
                        "last_error": None,
                        "calculate_ms": calculate_ms,
                        "master": self.master.summary(),
                    },
                }
                self._publish_status()
                return self._payload_unlocked(include_details=False)
        except Exception as error:
            with self.lock:
                self.metrics["theme_last_error"] = f"{type(error).__name__}: {error}"
                self.cache.setdefault("status", {})["last_error"] = self.metrics["theme_last_error"]
                self._publish_status()
                return self._payload_unlocked(include_details=False)

    def _payload_unlocked(self, *, include_details: bool) -> dict[str, Any]:
        if include_details:
            return deepcopy(self.cache)
        payload = {
            key: deepcopy(value)
            for key, value in self.cache.items()
            if key != "details"
        }
        for theme in payload.get("themes") or []:
            if isinstance(theme, dict):
                theme.pop("score_items", None)
                theme.pop("target_status", None)
        return payload

    def snapshot(self, *, include_details: bool = False) -> dict[str, Any]:
        with self.lock:
            self.metrics["theme_cache_hit_count"] += 1
            return self._payload_unlocked(include_details=include_details)

    def theme_detail(self, theme_id: str) -> dict[str, Any] | None:
        with self.lock:
            self.metrics["theme_cache_hit_count"] += 1
            detail = (self.cache.get("details") or {}).get(str(theme_id or "").strip().upper())
            return deepcopy(detail) if isinstance(detail, dict) else None

    def _publish_status(self) -> None:
        status = getattr(self.state, "status", None)
        if not isinstance(status, dict):
            return
        values = {
            **self.metrics,
            "theme_input_version": self.input_version,
            "theme_cache_version": self.cache_version,
            "theme_count": len(self.cache.get("themes") or []),
            "theme_cache_ready": bool((self.cache.get("status") or {}).get("ready")),
            "theme_cache_updated_at": self.cache.get("updated_at"),
        }
        state_lock = getattr(self.state, "lock", None)
        if state_lock is None:
            status.update(values)
        else:
            with state_lock:
                status.update(values)
