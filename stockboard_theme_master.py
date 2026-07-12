from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_THEME_MASTER_PATH = ROOT / "config" / "stockboard_theme_master.json"
_CODE_RE = re.compile(r"^\d{6}$")
_ALLOWED_ROLES = {"primary", "secondary"}


@dataclass(frozen=True)
class ThemeMember:
    stock_code: str
    role: str = "secondary"
    share_weight: float = 1.0


@dataclass(frozen=True)
class ThemeDefinition:
    theme_id: str
    theme_name: str
    members: tuple[ThemeMember, ...]
    active: bool = True
    aliases: tuple[str, ...] = ()


@dataclass
class ThemeMaster:
    themes: tuple[ThemeDefinition, ...]
    schema_version: int = 1
    updated_at: str = ""
    source_path: str = ""
    invalid_themes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def theme_by_id(self) -> dict[str, ThemeDefinition]:
        return {theme.theme_id: theme for theme in self.themes}

    @property
    def membership_count_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for theme in self.themes:
            for member in theme.members:
                counts[member.stock_code] = counts.get(member.stock_code, 0) + 1
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "source_path": self.source_path,
            "theme_count": len(self.themes),
            "member_count": sum(len(theme.members) for theme in self.themes),
            "unique_member_count": len(self.membership_count_by_code),
            "invalid_theme_count": len(self.invalid_themes),
            "warning_count": len(self.warnings),
        }


def _normalize_code(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def _clean_identifier(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9_]+", "_", text).strip("_")


def _parse_member(raw: Any, known_codes: set[str] | None) -> tuple[ThemeMember | None, str | None]:
    if not isinstance(raw, dict):
        return None, "member must be an object"
    code = _normalize_code(raw.get("stock_code"))
    if not _CODE_RE.fullmatch(code):
        return None, f"invalid stock_code: {raw.get('stock_code')!r}"
    if known_codes is not None and code not in known_codes:
        return None, f"unknown stock_code: {code}"
    role = str(raw.get("role") or "secondary").strip().lower()
    if role not in _ALLOWED_ROLES:
        return None, f"invalid role for {code}: {role}"
    try:
        share_weight = float(raw.get("share_weight", 1.0))
    except (TypeError, ValueError):
        return None, f"invalid share_weight for {code}"
    if not (share_weight > 0):
        return None, f"share_weight must be positive for {code}"
    return ThemeMember(stock_code=code, role=role, share_weight=share_weight), None


def load_theme_master(
    path: str | Path | None = None,
    *,
    known_codes: Iterable[str] | None = None,
) -> ThemeMaster:
    source = Path(path) if path is not None else DEFAULT_THEME_MASTER_PATH
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("theme master root must be an object")

    known_set = {_normalize_code(code) for code in known_codes} if known_codes is not None else None
    if known_set is not None:
        known_set.discard("")

    valid: list[ThemeDefinition] = []
    invalid: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_theme_ids: set[str] = set()
    raw_themes = payload.get("themes")
    if not isinstance(raw_themes, list):
        raise ValueError("themes must be a list")

    for index, raw_theme in enumerate(raw_themes):
        errors: list[str] = []
        if not isinstance(raw_theme, dict):
            invalid.append({"index": index, "errors": ["theme must be an object"]})
            continue

        theme_id = _clean_identifier(raw_theme.get("theme_id"))
        theme_name = str(raw_theme.get("theme_name") or "").strip()
        active = bool(raw_theme.get("active", True))
        if not theme_id:
            errors.append("missing theme_id")
        elif theme_id in seen_theme_ids:
            errors.append(f"duplicate theme_id: {theme_id}")
        if not theme_name:
            errors.append("missing theme_name")

        members: list[ThemeMember] = []
        seen_codes: set[str] = set()
        raw_members = raw_theme.get("members")
        if not isinstance(raw_members, list):
            errors.append("members must be a list")
            raw_members = []
        for raw_member in raw_members:
            member, error = _parse_member(raw_member, known_set)
            if error:
                warnings.append(f"{theme_id or index}: {error}")
                continue
            assert member is not None
            if member.stock_code in seen_codes:
                warnings.append(f"{theme_id}: duplicate member ignored: {member.stock_code}")
                continue
            seen_codes.add(member.stock_code)
            members.append(member)
        if active and not members:
            errors.append("active theme has no valid members")

        aliases_raw = raw_theme.get("aliases") or []
        aliases = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in aliases_raw
                if str(item).strip()
            )
        ) if isinstance(aliases_raw, list) else ()

        if errors:
            invalid.append({"index": index, "theme_id": theme_id, "theme_name": theme_name, "errors": errors})
            continue
        seen_theme_ids.add(theme_id)
        if active:
            valid.append(
                ThemeDefinition(
                    theme_id=theme_id,
                    theme_name=theme_name,
                    members=tuple(members),
                    active=True,
                    aliases=aliases,
                )
            )

    return ThemeMaster(
        themes=tuple(valid),
        schema_version=int(payload.get("schema_version") or 1),
        updated_at=str(payload.get("updated_at") or ""),
        source_path=str(source),
        invalid_themes=invalid,
        warnings=warnings,
    )
