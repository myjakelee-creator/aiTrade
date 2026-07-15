from __future__ import annotations

import time
from typing import Any

from realtime_v2.common import normalize_code, now_text


POLICY = "hot20_warm30_cold_stable_v1"


def _rank_lane(rank: int, hot_limit: int, warm_limit: int) -> str:
    if rank <= hot_limit:
        return "hot"
    if rank <= warm_limit:
        return "warm"
    return "cold"


def install() -> None:
    import stockboard_display_order as display_order

    if getattr(display_order, "_stockboard_three_lane_controller_installed", False):
        return

    base_class = display_order.DisplayOrderController

    class ThreeLaneDisplayOrderController(base_class):
        """Keep three stable display lanes while scores and target ranks keep updating."""

        def __init__(
            self,
            *args,
            warm_limit: int = 50,
            warm_out_rank: int = 60,
            warm_challenger_hold_sec: float = 5.0,
            warm_incumbent_out_hold_sec: float = 10.0,
            warm_swap_cooldown_sec: float = 5.0,
            **kwargs,
        ) -> None:
            super().__init__(*args, **kwargs)
            self.warm_limit = max(self.top_limit, int(warm_limit))
            self.warm_out_rank = max(self.warm_limit + 1, int(warm_out_rank))
            self.warm_challenger_hold_sec = max(0.0, float(warm_challenger_hold_sec))
            self.warm_incumbent_out_hold_sec = max(
                0.0, float(warm_incumbent_out_hold_sec)
            )
            self.warm_swap_cooldown_sec = max(0.0, float(warm_swap_cooldown_sec))

            self.warm_codes: list[str] = []
            self.cold_codes: list[str] = []
            self._warm_challenger_since: dict[str, float] = {}
            self._warm_incumbent_out_since: dict[str, float] = {}
            self._last_warm_swap_monotonic = 0.0
            self.warm_swap_count = 0
            self.last_warm_swap: dict[str, Any] | None = None

        @property
        def warm_capacity(self) -> int:
            return max(0, self.warm_limit - self.top_limit)

        def _sync_pool_compatibility_locked(self) -> None:
            self.pool_codes = [*self.warm_codes, *self.cold_codes]

        def _initialise_lanes_locked(self, ranked_codes: list[str]) -> None:
            self.top_codes = ranked_codes[: self.top_limit]
            self.warm_codes = ranked_codes[
                self.top_limit : self.warm_limit
            ]
            self.cold_codes = ranked_codes[self.warm_limit :]
            self._sync_pool_compatibility_locked()
            self.updated_at = now_text()
            self.version += 1

        def _sync_codes_locked(self, ranked_codes: list[str]) -> None:
            available = set(ranked_codes)
            self.top_codes = [code for code in self.top_codes if code in available]
            self.warm_codes = [
                code
                for code in self.warm_codes
                if code in available and code not in set(self.top_codes)
            ]
            known = set(self.top_codes) | set(self.warm_codes)
            self.cold_codes = [
                code
                for code in self.cold_codes
                if code in available and code not in known
            ]
            known |= set(self.cold_codes)

            # A newly appearing symbol starts outside HOT.  It can enter HOT only
            # through the guarded boundary transition below.
            for code in ranked_codes:
                if code not in known:
                    self.cold_codes.append(code)
                    known.add(code)

            while len(self.top_codes) < min(self.top_limit, len(ranked_codes)):
                source = self.warm_codes if self.warm_codes else self.cold_codes
                if not source:
                    break
                self.top_codes.append(source.pop(0))

            while len(self.warm_codes) < min(
                self.warm_capacity,
                max(0, len(ranked_codes) - len(self.top_codes)),
            ):
                if not self.cold_codes:
                    break
                self.warm_codes.append(self.cold_codes.pop(0))

            if len(self.warm_codes) > self.warm_capacity:
                overflow = self.warm_codes[self.warm_capacity :]
                self.warm_codes = self.warm_codes[: self.warm_capacity]
                self.cold_codes = [*overflow, *self.cold_codes]

            self._sync_pool_compatibility_locked()

        @staticmethod
        def _refresh_timer_map(
            timer_map: dict[str, float], active_codes: set[str], now: float
        ) -> None:
            for code in active_codes:
                timer_map.setdefault(code, now)
            for code in list(timer_map):
                if code not in active_codes:
                    timer_map.pop(code, None)

        def _update_lane_hold_times_locked(
            self, ranked_positions: dict[str, int], now: float
        ) -> None:
            hot_set = set(self.top_codes)
            hot_challengers = {
                code
                for code, rank in ranked_positions.items()
                if code not in hot_set and rank <= self.top_limit
            }
            hot_incumbents = {
                code
                for code in self.top_codes
                if ranked_positions.get(code, 10**9) > self.top_limit
            }
            self._refresh_timer_map(self._challenger_since, hot_challengers, now)
            self._refresh_timer_map(self._incumbent_out_since, hot_incumbents, now)

            warm_set = set(self.warm_codes)
            warm_challengers = {
                code
                for code in self.cold_codes
                if ranked_positions.get(code, 10**9) <= self.warm_limit
            }
            warm_incumbents = {
                code
                for code in self.warm_codes
                if ranked_positions.get(code, 10**9) > self.warm_limit
            }
            self._refresh_timer_map(
                self._warm_challenger_since, warm_challengers, now
            )
            self._refresh_timer_map(
                self._warm_incumbent_out_since, warm_incumbents, now
            )

        def _lane_list_for_code_locked(self, code: str) -> list[str] | None:
            if code in self.warm_codes:
                return self.warm_codes
            if code in self.cold_codes:
                return self.cold_codes
            if code in self.top_codes:
                return self.top_codes
            return None

        def _swap_codes_locked(
            self,
            left_lane: list[str],
            left_code: str,
            right_lane: list[str],
            right_code: str,
        ) -> None:
            left_index = left_lane.index(left_code)
            right_index = right_lane.index(right_code)
            left_lane[left_index] = right_code
            right_lane[right_index] = left_code
            self._sync_pool_compatibility_locked()

        def _maybe_hot_swap_locked(
            self,
            by_code: dict[str, dict[str, Any]],
            ranked_positions: dict[str, int],
            now: float,
        ) -> bool:
            if not self.top_codes or now - self._last_swap_monotonic < self.swap_cooldown_sec:
                return False

            challengers = [
                code
                for code in [*self.warm_codes, *self.cold_codes]
                if ranked_positions.get(code, 10**9) <= self.top_limit
            ]
            incumbents = [
                code
                for code in self.top_codes
                if ranked_positions.get(code, 10**9) > self.top_limit
            ]
            if not challengers or not incumbents:
                return False

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
            score_margin = self._score(by_code.get(challenger)) - self._score(
                by_code.get(incumbent)
            )
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
                return False

            challenger_lane = self._lane_list_for_code_locked(challenger)
            if challenger_lane is None:
                return False
            self._swap_codes_locked(self.top_codes, incumbent, challenger_lane, challenger)
            self._challenger_since.pop(challenger, None)
            self._incumbent_out_since.pop(incumbent, None)
            self._last_swap_monotonic = now
            self.swap_count += 1
            self.last_swap = {
                "promoted": challenger,
                "demoted": incumbent,
                "from_lane": "warm" if challenger_lane is self.warm_codes else "cold",
                "to_lane": "hot",
                "score_margin": round(score_margin, 2),
                "reason": "strong_margin" if strong_ready else "rank_hold",
                "at": now_text(),
            }
            self.updated_at = self.last_swap["at"]
            self.version += 1
            return True

        def _maybe_warm_swap_locked(
            self,
            by_code: dict[str, dict[str, Any]],
            ranked_positions: dict[str, int],
            now: float,
        ) -> bool:
            if (
                not self.warm_codes
                or not self.cold_codes
                or now - self._last_warm_swap_monotonic < self.warm_swap_cooldown_sec
            ):
                return False

            challengers = [
                code
                for code in self.cold_codes
                if ranked_positions.get(code, 10**9) <= self.warm_limit
            ]
            incumbents = [
                code
                for code in self.warm_codes
                if ranked_positions.get(code, 10**9) > self.warm_limit
            ]
            if not challengers or not incumbents:
                return False

            challenger = min(challengers, key=lambda code: ranked_positions.get(code, 10**9))
            incumbent = max(
                incumbents,
                key=lambda code: (
                    ranked_positions.get(code, 10**9),
                    -self._score(by_code.get(code)),
                ),
            )
            challenger_held = now - self._warm_challenger_since.get(challenger, now)
            incumbent_held = now - self._warm_incumbent_out_since.get(incumbent, now)
            score_margin = self._score(by_code.get(challenger)) - self._score(
                by_code.get(incumbent)
            )
            incumbent_rank = ranked_positions.get(incumbent, 10**9)

            normal_ready = (
                challenger_held >= self.warm_challenger_hold_sec
                and incumbent_rank >= self.warm_out_rank
                and incumbent_held >= self.warm_incumbent_out_hold_sec
            )
            strong_ready = (
                challenger_held >= self.strong_challenger_hold_sec
                and incumbent_held >= self.strong_challenger_hold_sec
                and score_margin >= self.strong_margin
            )
            if not (normal_ready or strong_ready):
                return False

            self._swap_codes_locked(self.warm_codes, incumbent, self.cold_codes, challenger)
            self._warm_challenger_since.pop(challenger, None)
            self._warm_incumbent_out_since.pop(incumbent, None)
            self._last_warm_swap_monotonic = now
            self.warm_swap_count += 1
            self.last_warm_swap = {
                "promoted": challenger,
                "demoted": incumbent,
                "from_lane": "cold",
                "to_lane": "warm",
                "score_margin": round(score_margin, 2),
                "reason": "strong_margin" if strong_ready else "rank_hold",
                "at": now_text(),
            }
            self.updated_at = self.last_warm_swap["at"]
            self.version += 1
            return True

        def _active_lane_locked(self, code: str) -> str:
            if code in self.top_codes:
                return "hot"
            if code in self.warm_codes:
                return "warm"
            return "cold"

        def _annotate(self, rows: list[dict[str, Any]], *, mode: str) -> list[dict[str, Any]]:
            slot_by_code: dict[str, int] = {}
            for lane_codes in (self.top_codes, self.warm_codes, self.cold_codes):
                for slot, code in enumerate(lane_codes, start=1):
                    slot_by_code[code] = slot

            for index, row in enumerate(rows, start=1):
                code = normalize_code(row.get("stock_code"))
                active_lane = self._active_lane_locked(code)
                try:
                    rank = int(row.get("selection_rank") or row.get("model_rank") or index)
                except (TypeError, ValueError):
                    rank = index
                target_lane = _rank_lane(rank, self.top_limit, self.warm_limit)

                row["display_order_rank"] = index
                row["display_order_paused"] = True
                row["display_order_mode"] = mode
                row["display_lane"] = "top20" if active_lane == "hot" else "top300"
                row["active_lane"] = active_lane
                row["target_lane"] = target_lane
                row["display_slot"] = slot_by_code.get(code)
                row["lane_pending"] = active_lane != target_lane
                row["update_priority"] = (
                    "fast" if active_lane == "hot" else "warm" if active_lane == "warm" else "cold"
                )
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

                if not self.top_codes and not self.warm_codes and not self.cold_codes:
                    self._initialise_lanes_locked(ranked_codes)
                self._sync_codes_locked(ranked_codes)

                ranked_positions = {
                    code: index for index, code in enumerate(ranked_codes, start=1)
                }
                now = time.monotonic()
                self._update_lane_hold_times_locked(ranked_positions, now)

                # Move at most one boundary pair in one calculation cycle.  This
                # prevents a burst of DOM membership changes from one score update.
                hot_changed = self._maybe_hot_swap_locked(
                    by_code, ranked_positions, now
                )
                if not hot_changed:
                    self._maybe_warm_swap_locked(by_code, ranked_positions, now)

                ordered_codes = [*self.top_codes, *self.warm_codes, *self.cold_codes]
                result = [by_code[code] for code in ordered_codes if code in by_code]
                return self._annotate(result, mode=POLICY)

        def status(self) -> dict[str, Any]:
            with self._lock:
                return {
                    "paused": True,
                    "manual_freeze": self.paused,
                    "mode": "manual_freeze" if self.paused else POLICY,
                    "pending_freeze": self.pending_freeze,
                    "frozen_count": len(self.frozen_codes),
                    "top20_count": len(self.top_codes),
                    "warm_count": len(self.warm_codes),
                    "cold_count": len(self.cold_codes),
                    "top300_count": len(self.warm_codes) + len(self.cold_codes),
                    "hot_pending_count": len(self._challenger_since),
                    "warm_pending_count": len(self._warm_challenger_since),
                    "swap_count": self.swap_count,
                    "warm_swap_count": self.warm_swap_count,
                    "last_swap": dict(self.last_swap) if self.last_swap else None,
                    "last_warm_swap": (
                        dict(self.last_warm_swap) if self.last_warm_swap else None
                    ),
                    "lane_policy": POLICY,
                    "updated_at": self.updated_at,
                    "version": self.version,
                }

    display_order.LegacyDisplayOrderController = base_class
    display_order.DisplayOrderController = ThreeLaneDisplayOrderController
    display_order._stockboard_three_lane_controller_installed = True
