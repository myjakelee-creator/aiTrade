from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "stockboard_market_calendar.json"

DEFAULT_WINDOWS = {
    "premarket_start": "08:00",
    "opening_call_start": "08:30",
    "regular_start": "09:00",
    "closing_call_start": "15:20",
    "regular_close": "15:30",
    "aftermarket_start": "15:40",
    "aftermarket_end": "20:00",
}


@dataclass(frozen=True)
class MarketSession:
    calendar_date: str
    trading_date: str
    phase: str
    phase_label: str
    is_trading_day: bool
    accept_realtime: bool
    prefer_seed_when_no_realtime: bool
    freeze_realtime_missing: bool
    reason: str
    windows: dict[str, str]
    special_day: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_config() -> dict[str, Any]:
    try:
        if CONFIG_PATH.is_file():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _parse_time(value: Any, fallback: str) -> time:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text[:2])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    except (TypeError, ValueError):
        pass
    hour_text, minute_text = fallback.split(":", 1)
    return time(int(hour_text), int(minute_text))


def _format_time(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _shift(value: time, minutes: int) -> time:
    base = datetime.combine(date.today(), value) + timedelta(minutes=minutes)
    return base.time().replace(second=0, microsecond=0)


def _windows_for_day(config: dict[str, Any], day_text: str) -> tuple[dict[str, str], dict[str, Any]]:
    defaults = {**DEFAULT_WINDOWS, **(config.get("default_windows") or {})}
    special_days = config.get("special_days") or {}
    special = special_days.get(day_text) if isinstance(special_days, dict) else None
    special = special if isinstance(special, dict) else {}
    windows = dict(defaults)
    explicit = special.get("windows")
    if isinstance(explicit, dict):
        windows.update({key: str(value) for key, value in explicit.items() if value})
    delay = int(special.get("open_delay_minutes") or 0)
    if delay:
        for key in (
            "premarket_start",
            "opening_call_start",
            "regular_start",
            "closing_call_start",
            "regular_close",
            "aftermarket_start",
            "aftermarket_end",
        ):
            windows[key] = _format_time(_shift(_parse_time(windows.get(key), DEFAULT_WINDOWS[key]), delay))
    return windows, special


def _is_trading_day(config: dict[str, Any], day: date) -> bool:
    day_text = day.strftime("%Y%m%d")
    if day.weekday() >= 5:
        return False
    holidays = set(str(value) for value in (config.get("holidays") or []))
    _windows, special = _windows_for_day(config, day_text)
    return day_text not in holidays and special.get("closed") is not True


def next_premarket_datetime(now: datetime | None = None, *, max_days: int = 370) -> datetime:
    """Return the next actual premarket boundary from the configured market calendar."""

    current = now or datetime.now()
    config = _load_config()
    for offset in range(max(1, int(max_days)) + 1):
        candidate_day = current.date() + timedelta(days=offset)
        if not _is_trading_day(config, candidate_day):
            continue
        day_text = candidate_day.strftime("%Y%m%d")
        windows, _special = _windows_for_day(config, day_text)
        premarket = datetime.combine(
            candidate_day,
            _parse_time(windows.get("premarket_start"), DEFAULT_WINDOWS["premarket_start"]),
        )
        if premarket > current:
            return premarket
    raise RuntimeError("next premarket not found within configured search range")


def last_completed_trading_date(now: datetime | None = None, *, max_days: int = 370) -> str:
    """Return the most recent trading date whose regular session has completed."""

    current = now or datetime.now()
    config = _load_config()
    for offset in range(max(1, int(max_days)) + 1):
        candidate_day = current.date() - timedelta(days=offset)
        if not _is_trading_day(config, candidate_day):
            continue
        if offset == 0:
            day_text = candidate_day.strftime("%Y%m%d")
            windows, _special = _windows_for_day(config, day_text)
            regular_close = datetime.combine(
                candidate_day,
                _parse_time(windows.get("regular_close"), DEFAULT_WINDOWS["regular_close"]),
            )
            if current < regular_close:
                continue
        return candidate_day.strftime("%Y%m%d")
    return ""


def _in_range(current: time, start: time, end: time) -> bool:
    return start <= current < end


def market_session_now(now: datetime | None = None) -> MarketSession:
    current = now or datetime.now()
    day_text = current.strftime("%Y%m%d")
    config = _load_config()
    holidays = set(str(day) for day in (config.get("holidays") or []))
    windows, special = _windows_for_day(config, day_text)
    current_time = current.time().replace(microsecond=0)

    if current.weekday() >= 5:
        return MarketSession(
            calendar_date=day_text,
            trading_date=day_text,
            phase="weekend",
            phase_label="주말 휴장",
            is_trading_day=False,
            accept_realtime=False,
            prefer_seed_when_no_realtime=True,
            freeze_realtime_missing=True,
            reason="weekend",
            windows=windows,
            special_day=special,
        )
    if day_text in holidays or special.get("closed") is True:
        return MarketSession(
            calendar_date=day_text,
            trading_date=day_text,
            phase="holiday",
            phase_label="휴장일",
            is_trading_day=False,
            accept_realtime=False,
            prefer_seed_when_no_realtime=True,
            freeze_realtime_missing=True,
            reason=str(special.get("reason") or "holiday"),
            windows=windows,
            special_day=special,
        )

    premarket_start = _parse_time(windows.get("premarket_start"), DEFAULT_WINDOWS["premarket_start"])
    opening_call_start = _parse_time(windows.get("opening_call_start"), DEFAULT_WINDOWS["opening_call_start"])
    regular_start = _parse_time(windows.get("regular_start"), DEFAULT_WINDOWS["regular_start"])
    closing_call_start = _parse_time(windows.get("closing_call_start"), DEFAULT_WINDOWS["closing_call_start"])
    regular_close = _parse_time(windows.get("regular_close"), DEFAULT_WINDOWS["regular_close"])
    aftermarket_start = _parse_time(windows.get("aftermarket_start"), DEFAULT_WINDOWS["aftermarket_start"])
    aftermarket_end = _parse_time(windows.get("aftermarket_end"), DEFAULT_WINDOWS["aftermarket_end"])

    if current_time < premarket_start:
        phase, label, accept, freeze = "before_market", "개장 전", False, True
    elif _in_range(current_time, premarket_start, opening_call_start):
        phase, label, accept, freeze = "premarket", "프리마켓", True, True
    elif _in_range(current_time, opening_call_start, regular_start):
        phase, label, accept, freeze = "opening_call", "장전 동시호가", True, True
    elif _in_range(current_time, regular_start, closing_call_start):
        phase, label, accept, freeze = "regular", "정규장", True, False
    elif _in_range(current_time, closing_call_start, regular_close):
        phase, label, accept, freeze = "closing_call", "장마감 동시호가", True, True
    elif _in_range(current_time, regular_close, aftermarket_start):
        phase, label, accept, freeze = "after_wait", "정규장 마감/애프터 대기", True, True
    elif _in_range(current_time, aftermarket_start, aftermarket_end):
        phase, label, accept, freeze = "aftermarket", "애프터마켓", True, False
    else:
        phase, label, accept, freeze = "closed", "장마감", False, True

    return MarketSession(
        calendar_date=day_text,
        trading_date=day_text,
        phase=phase,
        phase_label=label,
        is_trading_day=True,
        accept_realtime=accept,
        prefer_seed_when_no_realtime=True,
        freeze_realtime_missing=freeze,
        reason=str(special.get("reason") or phase),
        windows=windows,
        special_day=special,
    )


def market_session_dict(now: datetime | None = None) -> dict[str, Any]:
    return market_session_now(now).to_dict()
