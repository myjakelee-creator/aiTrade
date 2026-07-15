from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number, trading_date_text

PATCH_VERSION = "large_trade_stage4_lowload_v1"
SOURCE = "ka10055_rest_incremental"
STATUS = "partial_recent_page_since_activation"
PERSIST_KEYS = (
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
    "large_trade_trading_date",
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy_count = sell_count = 0
    buy_sum = sell_sum = 0.0
    for row in rows:
        if not isinstance(row, dict) or not row.get("is_large"):
            continue
        qty = _number(row.get("qty"))
        amount_eok = float(row.get("amount_krw") or 0) / 100_000_000
        if qty is not None and qty > 0:
            buy_count += 1
            buy_sum += amount_eok
        elif qty is not None and qty < 0:
            sell_count += 1
            sell_sum += amount_eok
    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_sum_eok": round(buy_sum, 4),
        "sell_sum_eok": round(sell_sum, 4),
    }


def install(base) -> None:
    """Make ka10055 restart-safe without adding QAx FID15 or extra threads."""

    import realtime_v2.worker_rest_live_metrics_patch as module

    state_class = getattr(base, "State", None)
    updater_class = module.RestLiveMetricUpdater
    if state_class is None or getattr(updater_class, "_stockboard_large_trade_stage4_installed", False):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *PERSIST_KEYS))
    )
    original_state_init = state_class.__init__
    original_apply = updater_class._apply

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["large_trade_stage4_installed"] = True
            self.status["large_trade_stage4_version"] = PATCH_VERSION

    def apply_large_trade_snapshot(
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
        values = {
            "large_trade_buy_count": int(buy_count),
            "large_trade_sell_count": int(sell_count),
            "large_trade_net_count": int(buy_count) - int(sell_count),
            "large_trade_buy_sum_eok": round(float(buy_sum_eok), 4),
            "large_trade_sell_sum_eok": round(float(sell_sum_eok), 4),
            "large_trade_net_sum_eok": round(
                float(buy_sum_eok) - float(sell_sum_eok),
                4,
            ),
            "large_trade_source": SOURCE,
            "large_trade_status": STATUS,
            "large_trade_threshold_krw": int(threshold_krw),
            "large_trade_updated_at": updated_at,
            "large_trade_trading_date": trading_date_text(),
        }
        with self.lock:
            quote = self._quote(code)
            daily = self.daily_values_by_code.setdefault(code, {})
            for target in (quote, daily):
                for key, value in values.items():
                    target[key] = deepcopy(value)
            self.status["large_trade_stage4_snapshot_count"] = int(
                self.status.get("large_trade_stage4_snapshot_count") or 0
            ) + 1
            self.status["large_trade_stage4_last_code"] = code
            self.status["large_trade_stage4_last_at"] = updated_at
            self._mark_daily_dirty()
        rebuild = getattr(self, "request_background_rebuild", None)
        if callable(rebuild):
            rebuild(reason="large_trade_stage4_snapshot", force=False)
        return 1

    def has_current_day_large_trade(self, code: str) -> bool:
        today = trading_date_text()
        with self.lock:
            sources = (
                dict(self.quotes.get(code) or {}),
                dict(self.daily_values_by_code.get(code) or {}),
            )
        for source in sources:
            source_date = _date_digits(
                source.get("large_trade_trading_date")
                or source.get("large_trade_updated_at")
            )
            if source_date != today:
                continue
            source_name = str(source.get("large_trade_source") or "")
            if source_name in {"", "new_session_reset"}:
                continue
            return True
        return False

    def patched_apply(self, code: str, metric: str, payload: dict[str, Any]) -> bool:
        if metric != "large_trade":
            return original_apply(self, code, metric, payload)

        normalized = normalize_code(code)
        config = self._metric_config("large_trade")
        threshold = int(config.get("threshold_krw") or 50_000_000)
        rows = module.parse_large_trade_page(payload, threshold_krw=threshold)
        keys = {str(row.get("key") or "") for row in rows if row.get("key")}
        timestamp = now_text()

        if normalized not in self.large_seen:
            self.large_seen[normalized] = keys
            if self.state.has_current_day_large_trade(normalized):
                self._status(
                    large_trade_stage4_first_mode="resume_existing_daily",
                    large_trade_stage4_first_code=normalized,
                    large_trade_stage4_first_row_count=len(rows),
                    large_trade_stage4_first_at=timestamp,
                )
                return True
            aggregate = _aggregate(rows)
            self.state.apply_large_trade_stage4_snapshot(
                normalized,
                threshold_krw=threshold,
                updated_at=timestamp,
                **aggregate,
            )
            self._status(
                large_trade_stage4_first_mode="initialize_from_first_page",
                large_trade_stage4_first_code=normalized,
                large_trade_stage4_first_row_count=len(rows),
                large_trade_stage4_first_at=timestamp,
            )
            return True

        seen = self.large_seen[normalized]
        new_rows = [row for row in rows if row.get("key") not in seen]
        seen.update(keys)
        if len(seen) > 2000:
            self.large_seen[normalized] = set(keys)
        aggregate = _aggregate(new_rows)
        if aggregate["buy_count"] or aggregate["sell_count"]:
            self.state.apply_rest_large_trade_delta(
                normalized,
                threshold_krw=threshold,
                updated_at=timestamp,
                **aggregate,
            )
            with self.state.lock:
                quote = self.state.quotes.get(normalized) or {}
                daily = self.state.daily_values_by_code.setdefault(normalized, {})
                for target in (quote, daily):
                    target["large_trade_source"] = SOURCE
                    target["large_trade_status"] = STATUS
                    target["large_trade_trading_date"] = trading_date_text()
                    for key in PERSIST_KEYS:
                        value = target.get(key)
                        if value not in (None, ""):
                            daily[key] = deepcopy(value)
                self.state._mark_daily_dirty()
        self._status(
            large_trade_stage4_last_new_rows=len(new_rows),
            large_trade_stage4_last_code=normalized,
            large_trade_stage4_last_at=timestamp,
        )
        return True

    state_class.__init__ = state_init
    state_class.apply_large_trade_stage4_snapshot = apply_large_trade_snapshot
    state_class.has_current_day_large_trade = has_current_day_large_trade
    updater_class._apply = patched_apply
    updater_class._stockboard_large_trade_stage4_installed = True
