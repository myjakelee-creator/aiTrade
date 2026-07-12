from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2 import after_close_recovery as recovery

GUARD_VERSION = "after_close_recovery_sampler_guard_v3"


def _metadata_current_exact(row: dict[str, Any], target_date: str) -> bool:
    metadata = row.get("source_metadata")
    if not isinstance(metadata, dict):
        return False
    for field in ("trade_value_1m_eok", "trade_value_5m_eok"):
        item = metadata.get(field)
        if not isinstance(item, dict):
            return False
        if item.get("value") in (None, ""):
            return False
        if str(item.get("source") or "") != "CLOSE_SAMPLER_EXACT":
            return False
        if recovery.date_text(item.get("trading_date")) != target_date:
            return False
        if bool(item.get("is_estimated")):
            return False
    return True


def _zero_volume_minute_result(rows, trading_date, market_scope, snapshot_at):
    target = recovery.date_text(trading_date)
    valid = []
    for row in rows:
        close = recovery.positive(row.get("close") or row.get("현재가"))
        volume = recovery.num(row.get("volume") or row.get("거래량"))
        stamp = str(row.get("time") or row.get("체결시간") or "")
        if close is None or volume is None or volume < 0:
            continue
        if target and recovery.date_text(stamp) and recovery.date_text(stamp) != target:
            continue
        valid.append(
            {
                "time": stamp,
                "close": abs(close),
                "volume": abs(volume),
                "open": abs(recovery.num(row.get("open") or row.get("시가")) or close),
                "high": abs(recovery.num(row.get("high") or row.get("고가")) or close),
                "low": abs(recovery.num(row.get("low") or row.get("저가")) or close),
            }
        )
    if not valid or any(item["volume"] > 0 for item in valid):
        return None
    valid.sort(
        key=lambda item: "".join(ch for ch in item["time"] if ch.isdigit()),
        reverse=True,
    )
    valid = valid[:5]
    latest = valid[0]
    oldest = valid[-1]
    basis = latest["time"] or snapshot_at
    coverage = min(1.0, len(valid) / 5)
    ohlc = {
        "open": round(oldest["open"]),
        "high": round(max(item["high"] for item in valid)),
        "low": round(min(item["low"] for item in valid)),
        "close": round(latest["close"]),
    }
    return {
        "minute_recovery_status": "ok",
        "minute_recovery_trade_status": "no_trade",
        "minute_recovery_display_text": "거래없음",
        "minute_recovery_error": None,
        "minute_recovery_snapshot_at": snapshot_at,
        "minute_recovery_trading_date": target,
        "minute_recovery_market_scope": market_scope,
        "minute_recovery_row_count": len(valid),
        "minute_close_price": round(latest["close"]),
        "minute_trade_value_1m_eok": 0.0,
        "minute_trade_value_5m_eok": 0.0,
        "minute_ohlc": ohlc,
        "minute_rows": [{**item, "amount_eok": 0.0} for item in valid],
        "recovery_values": {
            "price": recovery.source_value(
                round(latest["close"]),
                "MINUTE_BAR_CLOSE",
                "FALLBACK",
                basis,
                target,
                market_scope,
                0.90,
                False,
            ),
            "trade_value_1m_eok": recovery.source_value(
                0.0,
                "MINUTE_BAR_NO_TRADE",
                "NO_TRADE",
                basis,
                target,
                market_scope,
                0.95,
                False,
            ),
            "trade_value_5m_eok": recovery.source_value(
                0.0,
                "MINUTE_BAR_NO_TRADE",
                "NO_TRADE",
                basis,
                target,
                market_scope,
                0.95,
                False,
                coverage,
            ),
            "ohlc": recovery.source_value(
                ohlc,
                "MINUTE_BAR_OHLC",
                "FALLBACK",
                basis,
                target,
                market_scope,
                0.65,
                True,
                coverage,
            ),
        },
    }


def install_module_guard() -> None:
    coordinator = recovery.AfterCloseRecoveryCoordinator
    if getattr(coordinator, "_sampler_guard_installed", False):
        return

    original_minute_rows = recovery.minute_rows_to_recovery
    original_merge_theme = recovery.merge_sampler_theme

    def minute_rows_to_recovery(rows, trading_date, market_scope, snapshot_at):
        result = original_minute_rows(rows, trading_date, market_scope, snapshot_at)
        if str(result.get("minute_recovery_status") or "").lower() == "ok":
            return result
        no_trade = _zero_volume_minute_result(
            rows,
            trading_date,
            market_scope,
            snapshot_at,
        )
        return no_trade or result

    def merge_sampler_theme(payload, sampler):
        result = original_merge_theme(payload, sampler)
        for item in result.get("themes", []) if isinstance(result, dict) else []:
            if not isinstance(item, dict):
                continue
            for suffix in ("1m", "5m"):
                value = recovery.num(item.get(f"inflow_{suffix}_value"))
                coverage = recovery.num(item.get(f"inflow_{suffix}_coverage"))
                if value == 0 and coverage is not None and coverage > 0:
                    item[f"inflow_{suffix}_text"] = "거래없음"
                    item[f"inflow_{suffix}_tone"] = "zero"
                    item[f"inflow_{suffix}_recovery_label"] = "거래없음"
        return result

    recovery.minute_rows_to_recovery = minute_rows_to_recovery
    recovery.merge_sampler_theme = merge_sampler_theme

    original_needs = coordinator._needs
    original_stats = coordinator.stats

    def needs(self, row):
        result = original_needs(self, row)
        target_date = recovery.date_text(self.target_date())
        if target_date and _metadata_current_exact(row, target_date):
            return False, result[1], result[2], result[3], result[4]
        return result

    def stats(self):
        result = original_stats(self)
        result["sampler_guard"] = GUARD_VERSION
        return result

    coordinator._needs = needs
    coordinator.stats = stats
    coordinator._sampler_guard_installed = True


def install_theme_format_guard() -> None:
    from realtime_v2 import after_close_theme_recovery as theme_recovery

    if getattr(theme_recovery, "_no_trade_format_installed", False):
        return
    original_format = theme_recovery._format_eok

    def format_eok(value, estimated, partial):
        try:
            if float(value) == 0:
                return "거래없음"
        except (TypeError, ValueError):
            pass
        return original_format(value, estimated, partial)

    theme_recovery._format_eok = format_eok
    theme_recovery._no_trade_format_installed = True


def install_worker_guard(base) -> None:
    install_module_guard()
    if getattr(base, "_after_close_recovery_worker_guard_installed", False):
        return
    State = base.State
    original_quote = State._quote
    original_trade = State._apply_trade
    original_close = State._apply_close_metrics
    original_program = State.apply_program_net_values

    def metadata_dict(row):
        metadata = row.get("source_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            row["source_metadata"] = metadata
        return metadata

    def persist_metadata(state, code):
        row = state.quotes.get(code)
        if not isinstance(row, dict):
            return
        metadata = row.get("source_metadata")
        if not isinstance(metadata, dict) or not metadata:
            return
        entry = state.daily_values_by_code.setdefault(code, {})
        next_metadata = deepcopy(metadata)
        next_date = row.get("minute_recovery_trading_date")
        changed = entry.get("source_metadata") != next_metadata
        changed = changed or (
            next_date and entry.get("minute_recovery_trading_date") != next_date
        )
        if not changed:
            return
        entry["source_metadata"] = next_metadata
        if next_date:
            entry["minute_recovery_trading_date"] = next_date
        state._mark_daily_dirty()

    def quote_method(self, code):
        row = original_quote(self, code)
        metadata = metadata_dict(row)
        basis = row.get("large_trade_updated_at") or row.get("received_at")
        trading_date = recovery.date_text(basis) or base.trading_date_text()
        for field in ("large_trade_net_count", "large_trade_net_sum_eok"):
            if row.get(field) in (None, "") or field in metadata:
                continue
            metadata[field] = recovery.source_value(
                row[field],
                "PERSISTED_LAST_VALID",
                "HELD",
                basis or recovery.now_text(),
                trading_date,
                "AL_OR_DECLARED",
                0.8,
                False,
            )
        ohlc_metadata = metadata.get("ohlc")
        ohlc = row.get("ohlc")
        if isinstance(ohlc_metadata, dict) and isinstance(ohlc, dict):
            ohlc_date = recovery.date_text(
                ohlc.get("trading_date")
                or ohlc.get("date")
                or row.get("trading_date")
                or ohlc_metadata.get("basis_time")
            )
            if ohlc_date:
                ohlc_metadata["trading_date"] = ohlc_date
        return row

    def trade_method(self, event):
        original_trade(self, event)
        values = base.merged_event_values(event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        row = self.quotes.get(code) if code else None
        if not isinstance(row, dict):
            return
        basis = row.get("large_trade_updated_at")
        if not basis:
            return
        metadata = metadata_dict(row)
        changed = False
        trading_date = recovery.date_text(basis) or base.trading_date_text()
        for field in ("large_trade_net_count", "large_trade_net_sum_eok"):
            if row.get(field) in (None, ""):
                continue
            candidate = recovery.source_value(
                row[field],
                "COLLECTOR_AGGREGATE",
                "LIVE",
                basis,
                trading_date,
                "AL_OR_DECLARED",
                1.0,
                False,
            )
            preferred = recovery.prefer_source_value(
                metadata.get(field),
                candidate,
                allow_rollover=True,
            )
            if preferred != metadata.get(field):
                metadata[field] = preferred
                changed = True
        if changed:
            persist_metadata(self, code)

    def close_method(self, event):
        original_close(self, event)
        values = base.merged_event_values(event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        if code:
            persist_metadata(self, code)

    def program_method(self, values, source, status):
        result = original_program(self, values, source, status)
        with self.lock:
            for raw_code in (values or {}):
                code = base.normalize_code(raw_code)
                if code:
                    persist_metadata(self, code)
        return result

    State._quote = quote_method
    State._apply_trade = trade_method
    State._apply_close_metrics = close_method
    State.apply_program_net_values = program_method
    base._after_close_recovery_worker_guard_installed = True
