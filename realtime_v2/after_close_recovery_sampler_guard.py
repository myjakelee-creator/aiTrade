from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2 import after_close_recovery as recovery

GUARD_VERSION = "after_close_recovery_sampler_guard_v1"


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


def install_module_guard() -> None:
    coordinator = recovery.AfterCloseRecoveryCoordinator
    if getattr(coordinator, "_sampler_guard_installed", False):
        return
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


def install_worker_guard(base) -> None:
    install_module_guard()
    if getattr(base, "_after_close_recovery_worker_guard_installed", False):
        return
    State = base.State
    original_close = State._apply_close_metrics
    original_program = State.apply_program_net_values

    def persist_metadata(state, code):
        row = state.quotes.get(code)
        if not isinstance(row, dict):
            return
        metadata = row.get("source_metadata")
        if not isinstance(metadata, dict) or not metadata:
            return
        entry = state.daily_values_by_code.setdefault(code, {})
        entry["source_metadata"] = deepcopy(metadata)
        if row.get("minute_recovery_trading_date"):
            entry["minute_recovery_trading_date"] = row["minute_recovery_trading_date"]
        state._mark_daily_dirty()

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

    State._apply_close_metrics = close_method
    State.apply_program_net_values = program_method
    base._after_close_recovery_worker_guard_installed = True
