from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
DOCS_DIR = ROOT / "docs"
DEFAULT_HOST = os.getenv("STOCKBOARD_V2_HOST", "127.0.0.1")
DEFAULT_EVENT_PORT = int(os.getenv("STOCKBOARD_V2_EVENT_PORT", "8710"))
DEFAULT_WEB_PORT = int(os.getenv("STOCKBOARD_V2_WEB_PORT", "8765"))
KRW_PER_EOK = 100_000_000
MILLION_KRW_PER_EOK = 100
LARGE_TRADE_THRESHOLD_KRW = 50_000_000


def now_kst() -> datetime:
    return datetime.now(KST)


def now_text(timespec: str = "milliseconds") -> str:
    return now_kst().isoformat(timespec=timespec)


def trading_date_text() -> str:
    return now_kst().strftime("%Y%m%d")


def ensure_runtime_dir() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def to_number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def to_int(value: Any) -> int | None:
    number = to_number(value)
    if number is None:
        return None
    return int(number)


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("A") and len(text) == 7:
        text = text[1:]
    text = text.replace("_AL", "").replace("_NX", "")
    return text if len(text) == 6 and text.isdigit() else ""


def normalize_registered_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("A") and len(text) == 7:
        text = text[1:]
    return text


def normalized_price(raw: Any) -> int | None:
    value = to_number(raw)
    if value is None:
        return None
    return abs(int(value))


def normalized_rate(raw: Any) -> float | None:
    value = to_number(raw)
    if value is None:
        return None
    # Kiwoom real-data FID 12 can arrive either as -1.23 or -123.
    if abs(value) > 30:
        value = value / 100.0
    return round(value, 4)


def normalized_trade_value_eok(raw: Any) -> float | None:
    value = to_number(raw)
    if value is None:
        return None
    # FID 14 is commonly million KRW in Kiwoom real-data docs/examples.
    return round(value / MILLION_KRW_PER_EOK, 4)


def normalize_trade_time(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[0:2]}:{text[2:4]}:{text[4:6]}"
    return text


def event_age_sec(received_at: Any) -> float | None:
    if not received_at:
        return None
    try:
        dt = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return max(0.0, round((now_kst() - dt.astimezone(KST)).total_seconds(), 3))
    except (TypeError, ValueError):
        return None


def safe_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
