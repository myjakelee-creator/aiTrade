from __future__ import annotations

from threading import RLock
from typing import Any

from realtime_v2.common import normalize_code, now_text


class DisplayOrderController:
    """Keep display row positions stable without changing ranking scores.

    Ranking Engine still owns score/grade/model_rank.
    This controller only owns the final display order after ranking.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self.paused = False
        self.pending_freeze = False
        self.frozen_codes: list[str] = []
        self.updated_at: str | None = None
        self.version = 0

    def _code(self, row: dict[str, Any]) -> str:
        return normalize_code(row.get("stock_code"))

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
            if self.paused:
                return self.live()
            return self.freeze()

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

    def apply(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            if not self.paused:
                return rows

            if self.pending_freeze or not self.frozen_codes:
                self._capture_locked(rows)

            by_code: dict[str, dict[str, Any]] = {}
            for row in rows:
                code = self._code(row)
                if code:
                    by_code[code] = row

            result: list[dict[str, Any]] = []
            used: set[str] = set()
            next_order: list[str] = []

            for code in self.frozen_codes:
                row = by_code.get(code)
                if row is not None:
                    result.append(row)
                    used.add(code)
                    next_order.append(code)

            # New rows not present at freeze time are appended, not inserted above.
            for row in rows:
                code = self._code(row)
                if code and code not in used:
                    result.append(row)
                    used.add(code)
                    next_order.append(code)

            self.frozen_codes = next_order

            for index, row in enumerate(result, start=1):
                row["display_order_rank"] = index
                row["display_order_paused"] = True

            return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "paused": self.paused,
                "pending_freeze": self.pending_freeze,
                "frozen_count": len(self.frozen_codes),
                "updated_at": self.updated_at,
                "version": self.version,
            }
