from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.board_projection_runtime import LatestOnlyProjectionWorker
from realtime_v2.common import now_text


ROOT = Path(__file__).resolve().parents[1]


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _code(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text[-6:] if len(text) >= 6 else ""


class ThemeMembershipLoader:
    """Load a local theme membership map without making any TR request."""

    def __init__(self, paths: list[Path] | None = None, refresh_sec: float = 5.0):
        env_path = os.getenv("STOCKBOARD_THEME_MEMBERSHIP_FILE", "").strip()
        default_paths = [
            ROOT / "data" / "runtime" / "stockboard_v2" / "theme_membership.json",
            ROOT / "data" / "config" / "theme_membership.json",
            ROOT / "docs" / "assets" / "theme_membership.json",
        ]
        self.paths = paths or ([Path(env_path)] if env_path else []) + default_paths
        self.refresh_sec = max(1.0, float(refresh_sec))
        self._last_check_mono = 0.0
        self._last_mtime: float | None = None
        self._source: str | None = None
        self._themes: list[dict[str, Any]] = []
        self._last_error: str | None = None

    def _parse(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        themes: list[dict[str, Any]] = []
        raw_themes = payload.get("themes")
        if isinstance(raw_themes, list):
            for index, item in enumerate(raw_themes, start=1):
                if not isinstance(item, dict):
                    continue
                members = item.get("members") or item.get("stock_codes") or []
                codes = sorted({_code(value) for value in members if _code(value)})
                name = str(item.get("theme_name") or item.get("name") or "").strip()
                theme_id = str(
                    item.get("theme_id") or item.get("id") or name or index
                ).strip()
                if name and codes:
                    themes.append(
                        {"theme_id": theme_id, "theme_name": name, "members": codes}
                    )

        stock_to_themes = payload.get("stock_to_themes") or payload.get("membership")
        if isinstance(stock_to_themes, dict):
            by_name: dict[str, set[str]] = {}
            for raw_code, raw_names in stock_to_themes.items():
                code = _code(raw_code)
                if not code:
                    continue
                names = raw_names if isinstance(raw_names, list) else [raw_names]
                for raw_name in names:
                    name = str(raw_name or "").strip()
                    if name:
                        by_name.setdefault(name, set()).add(code)
            existing = {item["theme_name"] for item in themes}
            for name, codes in sorted(by_name.items()):
                if name not in existing and codes:
                    themes.append(
                        {
                            "theme_id": name,
                            "theme_name": name,
                            "members": sorted(codes),
                        }
                    )
        return themes

    def load(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        now_mono = time.monotonic()
        if now_mono - self._last_check_mono < self.refresh_sec:
            return self._themes, self.status()
        self._last_check_mono = now_mono

        source: Path | None = None
        for path in self.paths:
            try:
                if path.is_file():
                    source = path
                    break
            except OSError:
                continue

        if source is None:
            self._source = None
            self._themes = []
            self._last_mtime = None
            self._last_error = None
            return self._themes, self.status()

        try:
            mtime = source.stat().st_mtime
            if self._source == str(source) and self._last_mtime == mtime:
                return self._themes, self.status()
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("theme membership root must be an object")
            self._themes = self._parse(payload)
            self._source = str(source)
            self._last_mtime = mtime
            self._last_error = None
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
        return self._themes, self.status()

    def status(self) -> dict[str, Any]:
        return {
            "source": self._source,
            "theme_count": len(self._themes),
            "last_error": self._last_error,
            "candidate_paths": [str(path) for path in self.paths],
        }


class ThemeProjectionBuilder:
    """Aggregate shared stock feature rows into theme rows without TR calls."""

    def __init__(self, loader: ThemeMembershipLoader | None = None) -> None:
        self.loader = loader or ThemeMembershipLoader()

    def __call__(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        _meta: dict[str, Any],
    ) -> dict[str, Any]:
        themes, mapping_status = self.loader.load()
        by_code = {
            _code(row.get("stock_code")): row
            for row in rows
            if _code(row.get("stock_code"))
        }
        if not themes:
            return {
                "schema_version": 1,
                "source": "theme_projection_engine",
                "status": "WAIT_THEME_MAP",
                "ts": now_text(),
                "input_feature_version": feature_version,
                "theme_count": 0,
                "row_count": 0,
                "rows": [],
                "mapping": mapping_status,
                "policy": {
                    "direct_tr_allowed": False,
                    "input": "board_data_hub_shared_feature_snapshot",
                },
            }

        result: list[dict[str, Any]] = []
        for theme in themes:
            member_rows = [
                by_code[code] for code in theme["members"] if code in by_code
            ]
            if not member_rows:
                continue
            rates = [
                value
                for value in (_number(row.get("change_rate")) for row in member_rows)
                if value is not None
            ]
            values = [
                value
                for value in (
                    _number(row.get("trade_value_eok")) for row in member_rows
                )
                if value is not None
            ]
            scores = [
                value
                for value in (
                    _number(row.get("candidate_score") or row.get("grade_score"))
                    for row in member_rows
                )
                if value is not None
            ]
            top = max(
                member_rows,
                key=lambda row: (
                    _number(row.get("candidate_score") or row.get("grade_score"))
                    or 0.0,
                    _number(row.get("trade_value_eok")) or 0.0,
                ),
            )
            result.append(
                {
                    "theme_id": theme["theme_id"],
                    "theme_name": theme["theme_name"],
                    "member_count": len(theme["members"]),
                    "active_member_count": len(member_rows),
                    "advancers": sum(1 for value in rates if value > 0),
                    "decliners": sum(1 for value in rates if value < 0),
                    "avg_change_rate": (
                        round(sum(rates) / len(rates), 4) if rates else None
                    ),
                    "max_change_rate": round(max(rates), 4) if rates else None,
                    "trade_value_eok": round(sum(values), 4) if values else 0.0,
                    "avg_candidate_score": (
                        round(sum(scores) / len(scores), 4) if scores else None
                    ),
                    "max_candidate_score": round(max(scores), 4) if scores else None,
                    "top_stock_code": _code(top.get("stock_code")),
                    "top_stock_name": top.get("stock_name"),
                    "top_stock_change_rate": _number(top.get("change_rate")),
                    "top_stock_candidate_score": _number(
                        top.get("candidate_score") or top.get("grade_score")
                    ),
                }
            )

        result.sort(
            key=lambda row: (
                -(row.get("avg_candidate_score") or 0.0),
                -(row.get("trade_value_eok") or 0.0),
                str(row.get("theme_name") or ""),
            )
        )
        for rank, row in enumerate(result, start=1):
            row["rank"] = rank

        return {
            "schema_version": 1,
            "source": "theme_projection_engine",
            "status": "READY",
            "ts": now_text(),
            "input_feature_version": feature_version,
            "theme_count": len(result),
            "row_count": len(result),
            "rows": result,
            "mapping": mapping_status,
            "policy": {
                "direct_tr_allowed": False,
                "input": "board_data_hub_shared_feature_snapshot",
            },
        }


class ThemeProjectionRuntime:
    def __init__(self, hub: BoardDataHub, min_interval_ms: int = 500) -> None:
        self.worker = LatestOnlyProjectionWorker(
            name="theme",
            hub=hub,
            builder=ThemeProjectionBuilder(),
            min_interval_ms=min_interval_ms,
        )

    def start(self) -> None:
        self.worker.start()

    def submit(self, feature_version: int) -> None:
        self.worker.submit(feature_version)

    def stop(self) -> None:
        self.worker.stop()

    def status(self) -> dict[str, Any]:
        return self.worker.status()
