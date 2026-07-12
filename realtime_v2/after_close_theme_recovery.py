from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.after_close_recovery import date_text, num, now_text
from realtime_v2.market_session import last_completed_trading_date

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER_VERSION = "theme_after_close_recovery_v1"
FIELDS = ("trade_value_1m_eok", "trade_value_5m_eok")


def _format_eok(value: float, estimated: bool, partial: bool) -> str:
    rounded = round(value)
    signed = f"{'+' if rounded > 0 else ''}{rounded:,}억"
    return f"{'≈' if estimated else ''}{signed}{'*' if partial else ''}"


class ThemeAfterCloseRecoveryPublisher(threading.Thread):
    def __init__(
        self,
        state,
        theme_service,
        master_path: Path,
        *,
        debounce_sec: float = 2.0,
    ):
        super().__init__(name="theme-after-close-recovery", daemon=True)
        self.state = state
        self.theme_service = theme_service
        self.master_path = Path(master_path)
        self.debounce_sec = max(0.5, float(debounce_sec))
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.dirty = True
        self.theme_members: dict[str, tuple[tuple[str, float], ...]] = {}
        self.codes: set[str] = set()
        self.last_signature = None
        self.last_publish_at = None
        self.last_error = None
        self.publish_count = 0
        self.lock_busy_skip_count = 0
        self.last_target_date = None
        self.last_min_coverage = None
        self._load_master()

    def _load_master(self):
        try:
            from stockboard_theme_engine import load_theme_master

            master = load_theme_master(self.master_path)
            self.theme_members = {
                theme_id: tuple(
                    (str(code), float(weight))
                    for code, weight, _relation in members
                )
                for theme_id, members in master.by_theme.items()
            }
            self.codes = {
                code
                for members in self.theme_members.values()
                for code, _weight in members
            }
        except Exception as error:
            self.last_error = f"master: {type(error).__name__}: {error}"

    def mark_dirty(self):
        self.dirty = True
        self.wake.set()

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def status(self):
        return {
            "version": PUBLISHER_VERSION,
            "alive": self.is_alive(),
            "enabled": bool(self.theme_members and self.theme_service),
            "dirty": self.dirty,
            "publish_count": self.publish_count,
            "last_publish_at": self.last_publish_at,
            "last_target_date": self.last_target_date,
            "last_min_coverage": self.last_min_coverage,
            "lock_busy_skip_count": self.lock_busy_skip_count,
            "last_error": self.last_error,
        }

    def _copy_rows(self):
        lock = getattr(self.state, "lock", None)
        if lock is None:
            self.last_error = "state_lock_missing"
            return None
        try:
            acquired = lock.acquire(blocking=False)
        except TypeError:
            acquired = lock.acquire(False)
        if not acquired:
            self.lock_busy_skip_count += 1
            return None
        try:
            quotes = getattr(self.state, "quotes", {}) or {}
            rows = {}
            for code in self.codes:
                row = quotes.get(code)
                if not isinstance(row, dict):
                    continue
                rows[code] = {field: row.get(field) for field in FIELDS}
                metadata = row.get("source_metadata")
                rows[code]["source_metadata"] = (
                    deepcopy(metadata) if isinstance(metadata, dict) else {}
                )
        finally:
            lock.release()
        return rows

    @staticmethod
    def _field_item(row, field, target_date):
        value = num(row.get(field))
        metadata = (row.get("source_metadata") or {}).get(field)
        if value is None or value < 0 or not isinstance(metadata, dict):
            return None
        if date_text(metadata.get("trading_date")) != target_date:
            return None
        if metadata.get("value") in (None, ""):
            return None
        return {
            "value": value,
            "source": str(metadata.get("source") or "UNKNOWN"),
            "quality": float(metadata.get("quality") or 0),
            "is_estimated": bool(metadata.get("is_estimated")),
            "basis_time": str(metadata.get("basis_time") or ""),
            "market_scope": str(metadata.get("market_scope") or "UNKNOWN"),
        }

    def aggregate(self, rows, target_date):
        themes = {}
        for theme_id, members in self.theme_members.items():
            total_weight = sum(weight for _code, weight in members) or 1.0
            result = {}
            for field in FIELDS:
                weighted_value = 0.0
                covered_weight = 0.0
                weighted_quality = 0.0
                estimated = False
                sources = set()
                scopes = set()
                basis_times = []
                for code, weight in members:
                    item = self._field_item(rows.get(code) or {}, field, target_date)
                    if item is None:
                        continue
                    weighted_value += item["value"] * weight
                    covered_weight += weight
                    weighted_quality += item["quality"] * weight
                    estimated = estimated or item["is_estimated"]
                    sources.add(item["source"])
                    scopes.add(item["market_scope"])
                    if item["basis_time"]:
                        basis_times.append(item["basis_time"])
                if not covered_weight:
                    continue
                coverage = min(1.0, covered_weight / total_weight)
                source = (
                    next(iter(sources))
                    if len(sources) == 1
                    else "MIXED_AFTER_CLOSE_RECOVERY"
                )
                scope = next(iter(scopes)) if len(scopes) == 1 else "MIXED"
                result[field] = {
                    "value": round(weighted_value, 6),
                    "coverage": round(coverage, 4),
                    "quality": round(weighted_quality / covered_weight, 4),
                    "is_estimated": estimated,
                    "source": source,
                    "market_scope": scope,
                    "basis_time": max(basis_times) if basis_times else "",
                }
            if result:
                themes[theme_id] = result
        return themes

    @staticmethod
    def _signature(target_date, themes):
        compact = []
        for theme_id in sorted(themes):
            for field in FIELDS:
                item = themes[theme_id].get(field)
                if item:
                    compact.append(
                        (
                            theme_id,
                            field,
                            item["value"],
                            item["coverage"],
                            item["quality"],
                            item["is_estimated"],
                            item["source"],
                            item["market_scope"],
                            item["basis_time"],
                        )
                    )
        return target_date, tuple(compact)

    def publish(self, themes, target_date):
        service = self.theme_service
        if service is None or not themes:
            return False
        with service.lock:
            current = deepcopy(service.snapshot)
            display_themes = current.get("themes") if isinstance(current.get("themes"), list) else []
            if not display_themes:
                return False
            changed = False
            maxima = {}
            for field in FIELDS:
                maxima[field] = max(
                    [
                        (themes.get(str(item.get("theme_id"))) or {})
                        .get(field, {})
                        .get("value", 0)
                        for item in display_themes
                    ]
                    or [0]
                )
            coverages = []
            strongest = None
            for item in display_themes:
                theme_id = str(item.get("theme_id") or "")
                recovered = themes.get(theme_id) or {}
                for field, suffix in (
                    ("trade_value_1m_eok", "1m"),
                    ("trade_value_5m_eok", "5m"),
                ):
                    value_item = recovered.get(field)
                    if not value_item:
                        continue
                    value = float(value_item["value"])
                    coverage = float(value_item["coverage"])
                    estimated = bool(value_item["is_estimated"])
                    partial = coverage < 0.999
                    prefix = f"inflow_{suffix}"
                    next_values = {
                        f"{prefix}_value": round(value, 6),
                        f"{prefix}_text": _format_eok(value, estimated, partial),
                        f"{prefix}_tone": (
                            "plus" if value > 0 else "minus" if value < 0 else "zero"
                        ),
                        f"{prefix}_bar_pct": (
                            round(value / maxima[field] * 100, 1)
                            if maxima[field] > 0
                            else 0
                        ),
                        f"{prefix}_source": value_item["source"],
                        f"{prefix}_quality": value_item["quality"],
                        f"{prefix}_is_estimated": estimated,
                        f"{prefix}_coverage": coverage,
                        f"{prefix}_basis_time": value_item["basis_time"],
                        f"{prefix}_market_scope": value_item["market_scope"],
                        f"{prefix}_recovery_label": (
                            f"Coverage {coverage * 100:.0f}%"
                            if partial
                            else "정확값" if not estimated else "추정값"
                        ),
                    }
                    for key, next_value in next_values.items():
                        if item.get(key) != next_value:
                            item[key] = next_value
                            changed = True
                    coverages.append(coverage)
                    if suffix == "1m" and (strongest is None or value > strongest[0]):
                        strongest = (
                            value,
                            item.get("theme_name") or theme_id,
                            estimated,
                            partial,
                        )
            if not changed:
                return False
            summary = dict(current.get("summary") or {})
            if strongest:
                value, name, estimated, partial = strongest
                summary["strongest_flow_text"] = (
                    f"{name} {_format_eok(value, estimated, partial)}/1분"
                )
            current["summary"] = summary
            status = dict(current.get("status") or {})
            status.update(
                {
                    "after_close_recovery_source": PUBLISHER_VERSION,
                    "after_close_recovery_trading_date": target_date,
                    "after_close_recovery_updated_at": now_text(),
                    "after_close_recovery_min_coverage": (
                        round(min(coverages), 4) if coverages else None
                    ),
                }
            )
            current["status"] = status
            version = int(getattr(service, "cache_version", 0) or 0) + 1
            current["cache_version"] = version
            current["ts"] = now_text()
            service.cache_version = version
            service.snapshot = current
            service.snapshot_bytes = service.encode(current)
            service.last_payload_bytes = len(service.snapshot_bytes)
            service.last_updated_at = current["ts"]
            service.last_source_version = None
        try:
            service._persist_current(force=True)
        except Exception as error:
            self.last_error = f"theme_persist: {error}"
        self.publish_count += 1
        self.last_publish_at = now_text()
        self.last_target_date = target_date
        self.last_min_coverage = round(min(coverages), 4) if coverages else None
        return True

    def refresh(self):
        target_date = date_text(last_completed_trading_date(datetime.now()))
        if not target_date:
            return False
        rows = self._copy_rows()
        if rows is None:
            return False
        themes = self.aggregate(rows, target_date)
        signature = self._signature(target_date, themes)
        if signature == self.last_signature:
            self.dirty = False
            return False
        published = self.publish(themes, target_date)
        if published:
            self.last_signature = signature
            self.last_error = None
        self.dirty = False
        status = getattr(self.state, "status", None)
        if isinstance(status, dict):
            status["theme_after_close_recovery"] = self.status()
        return published

    def run(self):
        while not self.stop_event.is_set():
            self.wake.wait(self.debounce_sec)
            self.wake.clear()
            if self.stop_event.is_set():
                break
            if not self.dirty:
                continue
            time.sleep(self.debounce_sec)
            self.refresh()


def install(base) -> None:
    if getattr(base, "_theme_after_close_recovery_installed", False):
        return
    State = base.State
    original_close_metrics = State._apply_close_metrics
    original_server_init = base.WebServer.__init__
    original_server_close = base.WebServer.server_close

    def close_metrics(self, event):
        original_close_metrics(self, event)
        values = base.merged_event_values(event)
        if (
            str(values.get("minute_recovery_status") or "").lower() == "ok"
            or isinstance(values.get("recovery_values"), dict)
        ):
            publisher = getattr(self, "theme_after_close_recovery_publisher", None)
            if publisher:
                publisher.mark_dirty()

    def server_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        service = getattr(self, "theme_cache_service", None)
        publisher = ThemeAfterCloseRecoveryPublisher(
            state,
            service,
            ROOT / "config" / "stockboard_theme_master.json",
        )
        self.theme_after_close_recovery_publisher = publisher
        state.theme_after_close_recovery_publisher = publisher
        sampler = getattr(self, "close_window_sampler_service", None)
        if sampler is not None:
            sampler.theme_after_close_recovery_publisher = publisher
        publisher.start()
        publisher.mark_dirty()

    def server_close(self):
        publisher = getattr(self, "theme_after_close_recovery_publisher", None)
        if publisher:
            publisher.stop()
            publisher.join(timeout=2)
        return original_server_close(self)

    State._apply_close_metrics = close_metrics
    base.WebServer.__init__ = server_init
    base.WebServer.server_close = server_close
    base._theme_after_close_recovery_installed = True
