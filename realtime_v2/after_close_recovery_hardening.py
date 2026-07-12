from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2 import after_close_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]
HARDENING_VERSION = "after_close_recovery_hardening_v1"


def _static_theme_members() -> tuple[dict[str, tuple[tuple[str, float], ...]], str | None]:
    try:
        from stockboard_theme_engine import load_theme_master

        master = load_theme_master(ROOT / "config" / "stockboard_theme_master.json")
        return (
            {
                theme_id: tuple(
                    (str(code), float(weight))
                    for code, weight, _relation in members
                )
                for theme_id, members in master.by_theme.items()
            },
            None,
        )
    except Exception as error:
        return {}, f"{type(error).__name__}: {error}"


def _normalize_code(base, value: Any) -> str:
    try:
        return str(base.normalize_code(value) or "")
    except Exception:
        text = "".join(ch for ch in str(value or "") if ch.isdigit())
        return text[:6] if len(text) >= 6 else ""


def install_module_hardening() -> None:
    if getattr(recovery, "_after_close_recovery_hardening_installed", False):
        return

    original_minute_rows = recovery.minute_rows_to_recovery

    def minute_rows_to_recovery(rows, trading_date, market_scope, snapshot_at):
        result = original_minute_rows(rows, trading_date, market_scope, snapshot_at)
        result["minute_recovery_trading_date"] = recovery.date_text(trading_date)
        return result

    recovery.minute_rows_to_recovery = minute_rows_to_recovery

    coordinator = recovery.AfterCloseRecoveryCoordinator
    original_init = coordinator.__init__
    original_stats = coordinator.stats
    original_tick = coordinator.tick

    def coordinator_init(self, base, provider, scheduler_module):
        original_init(self, base, provider, scheduler_module)
        self.theme_members, self.theme_master_error = _static_theme_members()
        self.hardening_version = HARDENING_VERSION

    def theme_data(self):
        try:
            theme = self.scheduler_module._read_json_url(self.theme_url)
        except Exception as error:
            self.last_error = f"theme_snapshot: {error}"
            return {}
        self.theme_version = theme.get("cache_version")
        return theme if isinstance(theme, dict) else {}

    def build_plan(self, payload):
        rows = [
            row
            for row in (payload.get("rows", []) if isinstance(payload, dict) else [])
            if isinstance(row, dict)
        ]
        by_code = {
            _normalize_code(self.base, row.get("stock_code")): row
            for row in rows
            if _normalize_code(self.base, row.get("stock_code"))
        }
        theme = self._theme_data()
        themes = theme.get("themes", []) if isinstance(theme, dict) else []
        result: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(value, lane: int, reason: str) -> None:
            code = _normalize_code(self.base, value)
            if not code or code in seen:
                return
            seen.add(code)
            result.append(
                {
                    "stock_code": code,
                    "lane": lane,
                    "reason": reason,
                    "row": by_code.get(code, {}),
                }
            )

        try:
            selected = self.scheduler_module._load_selected(self.base)
        except Exception:
            selected = ""
        add(selected, 0, "s1")

        for row in rows[:20]:
            add(row.get("stock_code"), 1, "top20")

        for item in themes[:5]:
            if not isinstance(item, dict):
                continue
            for leader in item.get("leaders", []) or []:
                if isinstance(leader, dict):
                    add(leader.get("stock_code"), 2, "top_theme_leader")

        top_theme_ids = [
            str(item.get("theme_id") or "")
            for item in themes[:5]
            if isinstance(item, dict)
        ]
        other_theme_ids = [
            str(item.get("theme_id") or "")
            for item in themes[5:]
            if isinstance(item, dict)
        ]
        for lane, theme_ids in ((3, top_theme_ids), (4, other_theme_ids)):
            for theme_id in theme_ids:
                for code, _weight in self.theme_members.get(theme_id, ()):
                    add(code, lane, "theme_member")

        for index, row in enumerate(rows[20:], 21):
            try:
                rank = self.scheduler_module._model_rank(row, index)
            except Exception:
                rank = index
            if 21 <= int(rank or index) <= 50:
                add(row.get("stock_code"), 5, "hidden50")

        for row in rows:
            add(row.get("stock_code"), 6, "top300")
        return result

    def needs(self, row):
        target_date = recovery.date_text(self.target_date())
        recovered_date = recovery.date_text(
            row.get("minute_recovery_trading_date")
            or (row.get("recovery_values") or {}).get("trading_date")
            or row.get("minute_recovery_snapshot_at")
        )
        minute_ok = (
            str(row.get("minute_recovery_status") or "").lower() == "ok"
            and bool(target_date)
            and recovered_date == target_date
        )
        strength_5m, execution, orderbook = self.delegate._row_needs(self, row)
        return not minute_ok, strength_5m or execution, orderbook, strength_5m, execution

    def tick(self):
        if not getattr(self, "enabled", False):
            return
        now_mono = recovery.time.monotonic()
        if getattr(self, "current", None):
            self._observe(now_mono)
            if getattr(self, "current", None):
                return
        session = self.scheduler_module.market_session_now()
        current = datetime.now()
        self.phase = str(getattr(session, "phase", "unknown") or "unknown").lower()
        active, reason = self._active(session, current)
        if not active:
            self.mode = reason
            self.queue.clear()
            return
        return original_tick(self)

    def stats(self):
        result = original_stats(self)
        result.update(
            {
                "hardening": HARDENING_VERSION,
                "theme_master_member_count": sum(
                    len(members) for members in getattr(self, "theme_members", {}).values()
                ),
                "theme_master_error": getattr(self, "theme_master_error", None),
            }
        )
        return result

    coordinator.__init__ = coordinator_init
    coordinator._theme_data = theme_data
    coordinator._build_plan = build_plan
    coordinator._needs = needs
    coordinator.tick = tick
    coordinator.stats = stats

    sampler = recovery.CloseWindowSamplerService
    original_stop = sampler.stop
    original_finalize = sampler._finalize

    def sampler_stop(self):
        try:
            now = self.now_provider()
            if (
                self.active_key
                and self.active_end
                and self.active_date
                and now >= self.active_end
                and self.samples
            ):
                self._finalize(self.active_key, self.active_end, self.active_date)
                self.active_key = None
                self.active_end = None
                self.active_date = None
                self.samples.clear()
        finally:
            original_stop(self)

    def sampler_finalize(self, key, end_at, trading_date):
        original_finalize(self, key, end_at, trading_date)
        try:
            payload = json.loads(self.persist_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if recovery.date_text(payload.get("trading_date")) != recovery.date_text(trading_date):
            return

        basis = payload.get("basis_time") or end_at.isoformat(timespec="seconds")
        per_code = payload.get("per_code") if isinstance(payload.get("per_code"), dict) else {}
        state = self.state
        lock = getattr(state, "lock", None)
        if lock is not None:
            with lock:
                for raw_code, item in per_code.items():
                    if not isinstance(item, dict):
                        continue
                    code = _normalize_code(recovery, raw_code)
                    if not code:
                        continue
                    quote = getattr(state, "quotes", {}).get(code)
                    if quote is None and hasattr(state, "_quote"):
                        quote = state._quote(code)
                    if not isinstance(quote, dict):
                        continue
                    entry = getattr(state, "daily_values_by_code", {}).setdefault(code, {})
                    metadata = quote.setdefault("source_metadata", {})
                    scope = str(item.get("market_scope") or "UNKNOWN")
                    for field, source_key in (
                        ("trade_value_1m_eok", "trade_value_1m_eok"),
                        ("trade_value_5m_eok", "trade_value_5m_eok"),
                    ):
                        value = recovery.num(item.get(source_key))
                        if value is None:
                            continue
                        quote[field] = round(value, 6)
                        entry[field] = round(value, 6)
                        meta = recovery.source_value(
                            round(value, 6),
                            "CLOSE_SAMPLER_EXACT",
                            "EXACT",
                            basis,
                            trading_date,
                            scope,
                            1.0,
                            False,
                        )
                        metadata[field] = meta
                        entry.setdefault("source_metadata", {})[field] = deepcopy(meta)
                    quote["close_flow_sampler_trading_date"] = recovery.date_text(trading_date)
                    quote["close_flow_sampler_basis_time"] = basis
                    entry["close_flow_sampler_trading_date"] = recovery.date_text(trading_date)
                    entry["close_flow_sampler_basis_time"] = basis
                if hasattr(state, "_mark_daily_dirty"):
                    state._mark_daily_dirty()
        try:
            if hasattr(state, "persist_daily_state_if_needed"):
                state.persist_daily_state_if_needed(force=True)
        except Exception as error:
            self.last_error = f"daily_persist: {error}"

        theme_service = getattr(self, "theme_cache_service", None)
        if theme_service is None:
            return
        try:
            with theme_service.lock:
                current = deepcopy(theme_service.snapshot)
                merged = recovery.merge_sampler_theme(current, payload)
                if merged == current:
                    return
                version = int(getattr(theme_service, "cache_version", 0) or 0) + 1
                merged["cache_version"] = version
                merged["ts"] = recovery.now_text()
                for detail in getattr(theme_service, "details", {}).values():
                    if isinstance(detail, dict):
                        detail["cache_version"] = version
                theme_service.cache_version = version
                theme_service.snapshot = merged
                theme_service.snapshot_bytes = theme_service.encode(merged)
                theme_service.detail_bytes = {
                    name: theme_service.encode(detail)
                    for name, detail in getattr(theme_service, "details", {}).items()
                }
                theme_service.last_payload_bytes = len(theme_service.snapshot_bytes)
                theme_service.last_updated_at = merged["ts"]
                theme_service.last_source_version = None
        except Exception as error:
            self.last_error = f"theme_publish: {error}"

    sampler.stop = sampler_stop
    sampler._finalize = sampler_finalize
    recovery._after_close_recovery_hardening_installed = True


def install_worker_hardening(base, large=None) -> None:
    install_module_hardening()
    if getattr(base, "_after_close_recovery_worker_hardening_installed", False):
        return
    State = base.State
    original_quote = State._quote
    original_close = State._apply_close_metrics

    def quote_method(self, code):
        row = original_quote(self, code)
        persisted = self.daily_values_by_code.get(code) or {}
        for key in (
            "minute_recovery_trading_date",
            "close_flow_sampler_trading_date",
            "close_flow_sampler_basis_time",
        ):
            if key in persisted and key not in row:
                row[key] = persisted[key]
        metadata = row.setdefault("source_metadata", {})
        if row.get("ohlc") not in (None, "") and "ohlc" not in metadata:
            metadata["ohlc"] = recovery.source_value(
                deepcopy(row["ohlc"]),
                "PERSISTED_LAST_VALID",
                "HELD",
                row.get("ohlc_updated_at") or row.get("received_at") or recovery.now_text(),
                base.trading_date_text(),
                recovery.code_scope(row.get("source_code")),
                0.8,
                False,
            )
        return row

    def close_method(self, event):
        values = base.merged_event_values(event)
        original_close(self, event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        row = self.quotes.get(code) if code else None
        if not isinstance(row, dict):
            return
        metadata = row.setdefault("source_metadata", {})
        trading_date = (
            values.get("trading_date")
            or values.get("minute_recovery_trading_date")
            or base.trading_date_text()
        )

        strength_basis = (
            values.get("strength_completed_at")
            or values.get("strength_snapshot_at")
            or recovery.now_text()
        )
        if str(values.get("strength_status") or "").lower() == "ok":
            for field, raw_value in (
                (
                    "execution_strength",
                    values.get("execution_strength")
                    if values.get("execution_strength") not in (None, "")
                    else values.get("realtime_strength_snapshot"),
                ),
                ("strength_5m", values.get("strength_5m")),
                ("strength_20m", values.get("strength_20m")),
                ("strength_60m", values.get("strength_60m")),
            ):
                if raw_value not in (None, ""):
                    metadata[field] = recovery.prefer_source_value(
                        metadata.get(field),
                        recovery.source_value(
                            raw_value,
                            "OPT10046",
                            "EXACT",
                            strength_basis,
                            trading_date,
                            "AL_OR_DECLARED",
                            0.95,
                            False,
                        ),
                        allow_rollover=True,
                    )

        order_basis = (
            values.get("orderbook_completed_at")
            or values.get("orderbook_snapshot_at")
            or recovery.now_text()
        )
        if str(values.get("orderbook_status") or "").lower() == "ok":
            ratio = (
                values.get("bid_ask_ratio_snapshot")
                if values.get("bid_ask_ratio_snapshot") not in (None, "")
                else values.get("bid_ask_ratio")
            )
            if ratio not in (None, ""):
                metadata["bid_ask_ratio"] = recovery.prefer_source_value(
                    metadata.get("bid_ask_ratio"),
                    recovery.source_value(
                        ratio,
                        "OPT10004",
                        "EXACT",
                        order_basis,
                        trading_date,
                        "AL_OR_DECLARED",
                        0.95,
                        False,
                    ),
                    allow_rollover=True,
                )

        if values.get("minute_recovery_trading_date"):
            row["minute_recovery_trading_date"] = recovery.date_text(
                values["minute_recovery_trading_date"]
            )
        elif values.get("minute_recovery_status") == "ok":
            row["minute_recovery_trading_date"] = recovery.date_text(trading_date)

        if values.get("minute_recovery_status") == "ok":
            entry = self.daily_values_by_code.setdefault(code, {})
            entry["minute_recovery_trading_date"] = row.get(
                "minute_recovery_trading_date"
            )
            entry["source_metadata"] = deepcopy(metadata)
            self._mark_daily_dirty()

    State._quote = quote_method
    State._apply_close_metrics = close_method

    original_server_init = base.WebServer.__init__

    def server_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        sampler = getattr(self, "close_window_sampler_service", None)
        if sampler is not None:
            sampler.theme_cache_service = getattr(self, "theme_cache_service", None)

    base.WebServer.__init__ = server_init
    base._after_close_recovery_worker_hardening_installed = True


def install_collector_hardening(base) -> None:
    install_module_hardening()
    if getattr(base, "_after_close_recovery_collector_hardening_installed", False):
        return
    provider_class = base.KiwoomOpenApiRealtimeProvider
    original_status = provider_class.status

    def status(self):
        result = original_status(self)
        result = result if isinstance(result, dict) else {"status": result}
        result["after_close_recovery_hardening"] = HARDENING_VERSION
        return result

    provider_class.status = status
    base._after_close_recovery_collector_hardening_installed = True
