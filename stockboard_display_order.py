from __future__ import annotations

import time
from datetime import datetime
from threading import RLock
from typing import Any

from realtime_v2.common import normalize_code, now_text


class DisplayOrderController:
    """Keep Top20 and Top300 row positions stable while allowing guarded lane swaps.

    Ranking Engine still owns score/grade/model_rank. This controller owns only
    display membership/order. Internal positions never follow score changes.
    """

    def __init__(
        self,
        *,
        top_limit: int = 20,
        challenger_hold_sec: float = 5.0,
        strong_challenger_hold_sec: float = 3.0,
        incumbent_out_hold_sec: float = 10.0,
        strong_margin: float = 8.0,
        normal_out_rank: int = 30,
        swap_cooldown_sec: float = 5.0,
    ) -> None:
        self._lock = RLock()
        self.paused = False  # explicit hard freeze only
        self.pending_freeze = False
        self.frozen_codes: list[str] = []
        self.top_codes: list[str] = []
        self.pool_codes: list[str] = []
        self.updated_at: str | None = None
        self.version = 0

        self.top_limit = max(1, int(top_limit))
        self.challenger_hold_sec = max(0.0, float(challenger_hold_sec))
        self.strong_challenger_hold_sec = max(0.0, float(strong_challenger_hold_sec))
        self.incumbent_out_hold_sec = max(0.0, float(incumbent_out_hold_sec))
        self.strong_margin = max(0.0, float(strong_margin))
        self.normal_out_rank = max(self.top_limit + 1, int(normal_out_rank))
        self.swap_cooldown_sec = max(0.0, float(swap_cooldown_sec))

        self._challenger_since: dict[str, float] = {}
        self._incumbent_out_since: dict[str, float] = {}
        self._last_swap_monotonic = 0.0
        self.swap_count = 0
        self.last_swap: dict[str, Any] | None = None

    def _code(self, row: dict[str, Any]) -> str:
        return normalize_code(row.get("stock_code"))

    @staticmethod
    def _clear_cross_day_strength_fallback(row: dict[str, Any]) -> None:
        """Do not carry a previous daily file into a freshly restarted trading day.

        A process that stays alive may keep its in-memory value until the calendar-
        defined premarket produces a new snapshot. A new process must start blank.
        """
        if not row.get("previous_daily_display_fallback"):
            return
        snapshot_text = str(
            row.get("strength_completed_at")
            or row.get("strength_snapshot_at")
            or row.get("last_valid_strength_at")
            or ""
        )
        snapshot_date = "".join(ch for ch in snapshot_text[:10] if ch.isdigit())
        current_date = datetime.now().strftime("%Y%m%d")
        if snapshot_date == current_date:
            return
        for key in (
            "strength_5m",
            "strength_20m",
            "strength_60m",
            "last_valid_strength_5m",
        ):
            row.pop(key, None)
        row["strength_status"] = "new_trading_day_wait"
        row["strength_display_basis"] = "new_trading_day_wait"

    @staticmethod
    def _score(row: dict[str, Any] | None) -> float:
        if not isinstance(row, dict):
            return 0.0
        for key in ("candidate_score", "grade_score", "score_percent"):
            value = row.get(key)
            try:
                if value not in (None, ""):
                    return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def freeze(self) -> dict[str, Any]:
        with self._lock:
            self.paused = True
            self.pending_freeze = True
            self.updated_at = now_text()
            self.version += 1
            return self.status()

    def live(self) -> dict[str, Any]:
        with self._lock:
            self.paused = False
            self.pending_freeze = False
            self.frozen_codes = []
            self.updated_at = now_text()
            self.version += 1
            return self.status()

    def toggle(self) -> dict[str, Any]:
        with self._lock:
            return self.live() if self.paused else self.freeze()

    def _capture_locked(self, rows: list[dict[str, Any]]) -> None:
        order: list[str] = []
        seen: set[str] = set()
        for row in rows:
            code = self._code(row)
            if code and code not in seen:
                seen.add(code)
                order.append(code)
        self.frozen_codes = order
        self.pending_freeze = False
        self.updated_at = now_text()
        self.version += 1

    def _apply_manual_freeze_locked(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.pending_freeze or not self.frozen_codes:
            self._capture_locked(rows)
        by_code = {self._code(row): row for row in rows if self._code(row)}
        result: list[dict[str, Any]] = []
        used: set[str] = set()
        next_order: list[str] = []
        for code in self.frozen_codes:
            row = by_code.get(code)
            if row is not None:
                result.append(row)
                used.add(code)
                next_order.append(code)
        for row in rows:
            code = self._code(row)
            if code and code not in used:
                result.append(row)
                used.add(code)
                next_order.append(code)
        self.frozen_codes = next_order
        return self._annotate(result, mode="manual_freeze")

    def _initialise_lanes_locked(self, ranked_codes: list[str]) -> None:
        self.top_codes = ranked_codes[: self.top_limit]
        self.pool_codes = ranked_codes[self.top_limit :]
        self.updated_at = now_text()
        self.version += 1

    def _sync_codes_locked(self, ranked_codes: list[str]) -> None:
        available = set(ranked_codes)
        self.top_codes = [code for code in self.top_codes if code in available]
        top_set = set(self.top_codes)
        self.pool_codes = [
            code for code in self.pool_codes if code in available and code not in top_set
        ]
        known = top_set | set(self.pool_codes)
        for code in ranked_codes:
            if code not in known:
                self.pool_codes.append(code)
                known.add(code)
        while len(self.top_codes) < min(self.top_limit, len(ranked_codes)) and self.pool_codes:
            self.top_codes.append(self.pool_codes.pop(0))

    def _update_hold_times_locked(self, ranked_positions: dict[str, int], now: float) -> None:
        challenger_now = {
            code
            for code, rank in ranked_positions.items()
            if code not in set(self.top_codes) and rank <= self.top_limit
        }
        incumbent_now = {
            code
            for code in self.top_codes
            if ranked_positions.get(code, 10**9) >= self.top_limit + 1
        }
        for code in challenger_now:
            self._challenger_since.setdefault(code, now)
        for code in list(self._challenger_since):
            if code not in challenger_now:
                self._challenger_since.pop(code, None)
        for code in incumbent_now:
            self._incumbent_out_since.setdefault(code, now)
        for code in list(self._incumbent_out_since):
            if code not in incumbent_now:
                self._incumbent_out_since.pop(code, None)

    def _maybe_swap_locked(
        self,
        by_code: dict[str, dict[str, Any]],
        ranked_positions: dict[str, int],
        now: float,
    ) -> None:
        if not self.top_codes or not self.pool_codes:
            return
        if now - self._last_swap_monotonic < self.swap_cooldown_sec:
            return

        challengers = [
            code
            for code in self.pool_codes
            if ranked_positions.get(code, 10**9) <= self.top_limit
        ]
        incumbents = [
            code
            for code in self.top_codes
            if ranked_positions.get(code, 10**9) > self.top_limit
        ]
        if not challengers or not incumbents:
            return

        challenger = min(challengers, key=lambda code: ranked_positions.get(code, 10**9))
        incumbent = max(
            incumbents,
            key=lambda code: (
                ranked_positions.get(code, 10**9),
                -self._score(by_code.get(code)),
            ),
        )
        challenger_held = now - self._challenger_since.get(challenger, now)
        incumbent_held = now - self._incumbent_out_since.get(incumbent, now)
        score_margin = self._score(by_code.get(challenger)) - self._score(by_code.get(incumbent))
        incumbent_rank = ranked_positions.get(incumbent, 10**9)

        normal_ready = (
            challenger_held >= self.challenger_hold_sec
            and incumbent_rank >= self.normal_out_rank
            and incumbent_held >= self.incumbent_out_hold_sec
        )
        strong_ready = (
            challenger_held >= self.strong_challenger_hold_sec
            and incumbent_held >= self.strong_challenger_hold_sec
            and score_margin >= self.strong_margin
        )
        if not (normal_ready or strong_ready):
            return

        top_index = self.top_codes.index(incumbent)
        pool_index = self.pool_codes.index(challenger)
        self.top_codes[top_index] = challenger
        self.pool_codes[pool_index] = incumbent
        self._challenger_since.pop(challenger, None)
        self._incumbent_out_since.pop(incumbent, None)
        self._last_swap_monotonic = now
        self.swap_count += 1
        self.last_swap = {
            "promoted": challenger,
            "demoted": incumbent,
            "score_margin": round(score_margin, 2),
            "reason": "strong_margin" if strong_ready else "rank_hold",
            "at": now_text(),
        }
        self.updated_at = self.last_swap["at"]
        self.version += 1

    def _annotate(self, rows: list[dict[str, Any]], *, mode: str) -> list[dict[str, Any]]:
        for index, row in enumerate(rows, start=1):
            row["display_order_rank"] = index
            row["display_order_paused"] = True
            row["display_order_mode"] = mode
            row["display_lane"] = "top20" if index <= self.top_limit else "top300"
        return rows

    def apply(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            for row in rows:
                self._clear_cross_day_strength_fallback(row)
            if self.paused:
                return self._apply_manual_freeze_locked(rows)

            by_code: dict[str, dict[str, Any]] = {}
            ranked_codes: list[str] = []
            for row in rows:
                code = self._code(row)
                if code and code not in by_code:
                    by_code[code] = row
                    ranked_codes.append(code)
            if not ranked_codes:
                return []
            if not self.top_codes and not self.pool_codes:
                self._initialise_lanes_locked(ranked_codes)
            self._sync_codes_locked(ranked_codes)

            ranked_positions = {code: index for index, code in enumerate(ranked_codes, start=1)}
            now = time.monotonic()
            self._update_hold_times_locked(ranked_positions, now)
            self._maybe_swap_locked(by_code, ranked_positions, now)

            ordered_codes = [*self.top_codes, *self.pool_codes]
            result = [by_code[code] for code in ordered_codes if code in by_code]
            return self._annotate(result, mode="lane_stable")

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                # Browser must consume worker order and must not score-sort again.
                "paused": True,
                "manual_freeze": self.paused,
                "mode": "manual_freeze" if self.paused else "lane_stable",
                "pending_freeze": self.pending_freeze,
                "frozen_count": len(self.frozen_codes),
                "top20_count": len(self.top_codes),
                "top300_count": len(self.pool_codes),
                "swap_count": self.swap_count,
                "last_swap": dict(self.last_swap) if self.last_swap else None,
                "updated_at": self.updated_at,
                "version": self.version,
            }
