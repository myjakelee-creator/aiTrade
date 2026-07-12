from __future__ import annotations

from realtime_v2 import after_close_recovery as recovery

POLICY_GUARD_VERSION = "after_close_recovery_policy_guard_v3"


def install() -> None:
    if getattr(recovery, "_after_close_recovery_policy_guard_installed", False):
        return

    sampler = recovery.CloseWindowSamplerService

    @staticmethod
    def nearest_before(samples, target):
        before = [sample for sample in samples if sample[0] <= target]
        return max(before, key=lambda item: item[0]) if before else None

    sampler._nearest = nearest_before

    coordinator = recovery.AfterCloseRecoveryCoordinator
    original_build_plan = coordinator._build_plan
    original_refresh = coordinator._refresh
    original_tick = coordinator.tick
    original_stats = coordinator.stats

    def build_plan(self, payload):
        plan = original_build_plan(self, payload)
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        valid_codes = {
            self.base.normalize_code(row.get("stock_code"))
            for row in rows
            if isinstance(row, dict)
            and self.base.normalize_code(row.get("stock_code"))
        }
        filtered = [item for item in plan if item.get("stock_code") in valid_codes]
        self.outside_universe_filtered_count = len(plan) - len(filtered)
        return filtered

    def refresh(self, now_mono):
        original_refresh(self, now_mono)
        if not getattr(self, "plan", None):
            return

        existing = set(getattr(self, "queue", ()))
        if getattr(self, "current", None):
            existing.add(self.current)

        target_date = self.target_date()
        added = 0
        missing_total = 0
        for item in self.plan:
            code = item["stock_code"]
            row = item["row"]
            try:
                need_order = bool(self._needs(row)[2])
            except Exception:
                need_order = False
            if not need_order:
                continue
            missing_total += 1
            task = ("orderbook", code)
            if task in existing:
                continue
            if ("orderbook", code, target_date) in self.completed:
                continue
            if now_mono < self.retry_at.get(task, 0):
                continue
            if now_mono < self.settle_until.get(task, 0):
                continue
            self.queue.append(task)
            existing.add(task)
            added += 1

        self.missing_orderbook_count = missing_total
        self.orderbook_all_lane_enqueue_count = int(
            getattr(self, "orderbook_all_lane_enqueue_count", 0)
        ) + added
        if added and self.mode == "complete":
            self.mode = "queued_orderbook_all_lanes"

    @staticmethod
    def terminal_label(row, kind):
        if kind == "minute":
            if str(row.get("minute_recovery_trade_status") or "").lower() == "no_trade":
                return "거래없음"
            has_value = any(
                recovery.num(row.get(key)) is not None
                for key in ("trade_value_1m_eok", "trade_value_5m_eok")
            )
            return "직전값" if has_value else "복구실패"
        if kind == "strength":
            has_value = any(
                recovery.positive(row.get(key)) is not None
                for key in ("execution_strength", "strength_5m")
            )
            return "직전값" if has_value else "복구실패"
        has_value = recovery.positive(row.get("bid_ask_ratio")) is not None
        return "직전값" if has_value else "복구실패"

    def mark_terminal(self, target_date):
        store = getattr(self.provider, "store", None)
        if store is None:
            return
        marked = 0
        failed = 0
        held = 0
        no_trade = 0
        for item in getattr(self, "plan", ()):
            code = item.get("stock_code")
            row = item.get("row") if isinstance(item.get("row"), dict) else {}
            try:
                need_minute, need_strength, need_order, _s5, _execution = self._needs(row)
            except Exception:
                continue
            labels = {}
            for kind, needed in (
                ("minute", need_minute),
                ("strength", need_strength),
                ("orderbook", need_order),
            ):
                if not needed:
                    continue
                label = terminal_label(row, kind)
                labels[kind] = label
                failed += int(label == "복구실패")
                held += int(label == "직전값")
                no_trade += int(label == "거래없음")
            if not labels:
                continue
            overall = (
                "복구실패"
                if "복구실패" in labels.values()
                else "직전값"
                if "직전값" in labels.values()
                else "거래없음"
            )
            store.update_close_metrics(
                code,
                {
                    "recovery_terminal_status": labels,
                    "recovery_display_status": overall,
                    "recovery_terminal_at": recovery.now_text(),
                    "recovery_terminal_trading_date": target_date,
                },
            )
            marked += 1
        self.terminal_marked_date = target_date
        self.terminal_marked_count = marked
        self.terminal_failed_field_count = failed
        self.terminal_held_field_count = held
        self.terminal_no_trade_field_count = no_trade

    def tick(self):
        target_date = self.target_date()
        if getattr(self, "terminal_marked_date", None) not in (None, target_date):
            self.terminal_marked_date = None
            self.terminal_marked_count = 0
        original_tick(self)
        if (
            self.mode == "premarket_cutoff"
            and target_date
            and getattr(self, "terminal_marked_date", None) != target_date
        ):
            mark_terminal(self, target_date)

    def stats(self):
        result = original_stats(self)
        result.update(
            {
                "policy_guard": POLICY_GUARD_VERSION,
                "sampler_nearest_policy": "target_time_or_earlier_only",
                "orderbook_recovery_lanes": "P0-P6",
                "orderbook_all_lane_enqueue_count": int(
                    getattr(self, "orderbook_all_lane_enqueue_count", 0)
                ),
                "outside_universe_filtered_count": int(
                    getattr(self, "outside_universe_filtered_count", 0)
                ),
                "terminal_marked_date": getattr(self, "terminal_marked_date", None),
                "terminal_marked_count": int(
                    getattr(self, "terminal_marked_count", 0)
                ),
                "terminal_failed_field_count": int(
                    getattr(self, "terminal_failed_field_count", 0)
                ),
                "terminal_held_field_count": int(
                    getattr(self, "terminal_held_field_count", 0)
                ),
                "terminal_no_trade_field_count": int(
                    getattr(self, "terminal_no_trade_field_count", 0)
                ),
            }
        )
        return result

    coordinator._build_plan = build_plan
    coordinator._refresh = refresh
    coordinator.tick = tick
    coordinator.stats = stats
    recovery._after_close_recovery_policy_guard_installed = True
