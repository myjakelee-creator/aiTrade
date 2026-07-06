"""Previous trade-value enrichment for StockBoard rows.

The net-buy-strength ranking model needs a stable previous trade value.
This module derives it from the already-used ka10086 daily rows, persists it
under data/runtime, and injects it into each display row before ranking.

It also provides a safe previous-rank mode: Kiwoom ka10032 previous-day input is
not verified in our local docs/code, so previous rank is produced by sorting the
current tradable universe by ka10086 previous trade value after OHLC enrichment.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any

KST = timezone(timedelta(hours=9))
KRW_PER_EOK = Decimal("100000000")
MILLION_KRW_PER_EOK = Decimal("100")
CACHE_SCHEMA_VERSION = 1
PREVIOUS_RANK_MARKER = "_stockboard_previous_rank_requested"
_CACHE_LOCK = RLock()


def _first(row: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(row, dict):
        return None
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return float(number)


def _date_from_daily_row(row: dict[str, Any] | None) -> str:
    value = _first(row, "date", "dt", "일자")
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits if len(digits) == 8 else ""


def _runtime_dir() -> Path:
    configured = os.getenv("STOCKBOARD_RUNTIME_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parent / "data" / "runtime"


def _cache_path(query_date: str | None) -> Path:
    date_text = "".join(character for character in str(query_date or "") if character.isdigit())[:8]
    if len(date_text) != 8:
        date_text = datetime.now(KST).strftime("%Y%m%d")
    return _runtime_dir() / f"previous_trade_value_{date_text}.json"


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            payload["entries"] = {}
        return payload
    except (OSError, json.JSONDecodeError) as error:
        print(f"warning: previous trade value cache load failed: {error}", file=sys.stderr)
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}


def _save_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path.replace(path)
    except OSError as error:
        print(f"warning: previous trade value cache save failed: {error}", file=sys.stderr)


def _decimal_from_value(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def previous_trade_value_from_daily_row(previous_row: dict[str, Any] | None) -> dict[str, Any]:
    """Return previous trade value in eok units from a ka10086 daily row."""
    if not isinstance(previous_row, dict):
        return {"prev_trade_value_eok": None, "prev_trade_value_status": "missing"}

    previous_date = _date_from_daily_row(previous_row)
    amount_million = _decimal_from_value(
        _first(previous_row, "amt_mn", "trade_value_mn", "trde_prica", "trade_value")
    )
    if amount_million is not None and amount_million > 0:
        value_eok = amount_million / MILLION_KRW_PER_EOK
        return {
            "prev_trade_value_eok": float(value_eok),
            "prev_trade_value_source": "ka10086_amt_mn",
            "prev_trade_value_status": "ok",
            "prev_trade_value_date": previous_date,
            "prev_trade_value_raw_million": float(amount_million),
        }

    close_price = _decimal_from_value(_first(previous_row, "close_pric", "close"))
    trade_quantity = _decimal_from_value(_first(previous_row, "trde_qty", "trade_quantity"))
    if close_price is not None and close_price > 0 and trade_quantity is not None and trade_quantity > 0:
        value_eok = abs(close_price) * trade_quantity / KRW_PER_EOK
        return {
            "prev_trade_value_eok": float(value_eok),
            "prev_trade_value_source": "prev_close_x_prev_volume",
            "prev_trade_value_status": "calculated",
            "prev_trade_value_date": previous_date,
            "prev_close_price": float(abs(close_price)),
            "prev_trade_quantity": float(trade_quantity),
        }

    return {
        "prev_trade_value_eok": None,
        "prev_trade_value_source": "unavailable",
        "prev_trade_value_status": "unavailable",
        "prev_trade_value_date": previous_date,
    }


def _copy_prev_trade_value_to_row(row: dict[str, Any], value_data: dict[str, Any]) -> bool:
    value = _number_or_none(value_data.get("prev_trade_value_eok"))
    if value is None or value <= 0:
        return False
    row["prev_trade_value_eok"] = value
    row["previous_trade_value_eok"] = value
    row["yesterday_trade_value_eok"] = value
    for key in (
        "prev_trade_value_source",
        "prev_trade_value_status",
        "prev_trade_value_date",
        "prev_trade_value_raw_million",
        "prev_close_price",
        "prev_trade_quantity",
    ):
        if key in value_data:
            row[key] = value_data.get(key)
    return True


def _entry_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    value = _number_or_none(row.get("prev_trade_value_eok"))
    if value is None or value <= 0:
        return None
    return {
        "prev_trade_value_eok": value,
        "prev_trade_value_source": row.get("prev_trade_value_source"),
        "prev_trade_value_status": row.get("prev_trade_value_status"),
        "prev_trade_value_date": row.get("prev_trade_value_date"),
        "stock_code": row.get("stock_code"),
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
    }


def _original_rank_value(row: dict[str, Any]) -> int:
    value = _number_or_none(row.get("original_rank"))
    if value is None:
        value = _number_or_none(row.get("rank"))
    if value is None:
        return 999999
    return int(value)


def _sort_rows_by_previous_trade_value(rows: list[dict[str, Any]]) -> int:
    sortable_rows = [row for row in rows if isinstance(row, dict)]
    if not sortable_rows:
        return 0

    def sort_key(row: dict[str, Any]):
        value = _number_or_none(row.get("prev_trade_value_eok"))
        has_value = value is not None and value > 0
        return (
            0 if has_value else 1,
            -(value or 0),
            _original_rank_value(row),
            str(row.get("stock_code") or ""),
        )

    rows.sort(key=sort_key)
    ranked_count = 0
    for rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        row["rank"] = rank
        row["displayed_rank"] = rank
        row["previous_trade_value_rank"] = rank
        row["rank_basis_source"] = "ka10086_previous_trade_value"
        row["rank_basis_sort_key"] = "prev_trade_value_eok_desc"
        if _number_or_none(row.get("prev_trade_value_eok")) is not None:
            ranked_count += 1
    return ranked_count


def install_previous_trade_value_patch() -> None:
    import kiwoom_data_provider

    if getattr(kiwoom_data_provider, "_previous_trade_value_patch_installed", False):
        return

    original_build_ohlc = kiwoom_data_provider._build_ohlc
    original_fetch_ohlc = kiwoom_data_provider.fetch_ohlc
    original_fetch_trade_value_top100 = kiwoom_data_provider.fetch_trade_value_top100

    def fetch_trade_value_top100_with_previous_rank(access_token, rank_basis="today"):
        basis = str(rank_basis or "today").strip().lower()
        if basis != "previous":
            return original_fetch_trade_value_top100(access_token, rank_basis)
        rows, page_counts = original_fetch_trade_value_top100(access_token, "today")
        for row in rows:
            if isinstance(row, dict):
                row[PREVIOUS_RANK_MARKER] = True
        return rows, page_counts

    def build_ohlc_with_previous_trade_value(current_row, previous_row):
        ohlc, sample = original_build_ohlc(current_row, previous_row)
        value_data = previous_trade_value_from_daily_row(previous_row)
        if isinstance(ohlc, dict) and value_data.get("prev_trade_value_eok"):
            ohlc.update(value_data)
        if isinstance(sample, dict):
            sample.update(
                {
                    "prev_trade_value_eok": value_data.get("prev_trade_value_eok"),
                    "prev_trade_value_source": value_data.get("prev_trade_value_source"),
                    "prev_trade_value_status": value_data.get("prev_trade_value_status"),
                    "prev_trade_value_date": value_data.get("prev_trade_value_date"),
                }
            )
        return ohlc, sample

    def fetch_ohlc_with_previous_trade_value(access_token, rows, query_date, sleep_seconds, sequential=False):
        cache_file = _cache_path(query_date)
        with _CACHE_LOCK:
            cache_payload = _load_cache(cache_file)
        previous_rank_requested = any(
            isinstance(row, dict) and row.get(PREVIOUS_RANK_MARKER) for row in rows
        )
        result = original_fetch_ohlc(access_token, rows, query_date, sleep_seconds, sequential=sequential)
        entries = cache_payload.setdefault("entries", {})
        attached = 0
        cached = 0
        fallback = 0
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            stock_code = str(row.get("stock_code") or "").strip()
            ohlc = row.get("ohlc")
            if isinstance(ohlc, dict) and _copy_prev_trade_value_to_row(row, ohlc):
                entry = _entry_from_row(row)
                if stock_code and entry is not None:
                    entries[stock_code] = entry
                    changed = True
                attached += 1
                continue
            entry = entries.get(stock_code) if stock_code else None
            if isinstance(entry, dict) and _copy_prev_trade_value_to_row(row, {**entry, "prev_trade_value_status": "cached"}):
                cached += 1
                continue
            row.setdefault("prev_trade_value_source", "unavailable")
            row.setdefault("prev_trade_value_status", "fallback")
            row.setdefault("prev_trade_value_fallback_score", 60)
            fallback += 1
        cache_payload["schema_version"] = CACHE_SCHEMA_VERSION
        cache_payload["query_date"] = query_date
        cache_payload["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
        cache_payload.setdefault("source", "ka10086_previous_daily_row")
        if changed:
            with _CACHE_LOCK:
                _save_cache(cache_file, cache_payload)
        previous_rank_sorted_count = 0
        if previous_rank_requested:
            previous_rank_sorted_count = _sort_rows_by_previous_trade_value(rows)
        if isinstance(result, dict):
            result["previous_trade_value"] = {
                "attached_count": attached,
                "cached_count": cached,
                "fallback_count": fallback,
                "cache_file": str(cache_file),
                "previous_rank_requested": previous_rank_requested,
                "previous_rank_sorted_count": previous_rank_sorted_count,
                "previous_rank_sort_key": "prev_trade_value_eok_desc" if previous_rank_requested else None,
            }
        return result

    kiwoom_data_provider.fetch_trade_value_top100 = fetch_trade_value_top100_with_previous_rank
    kiwoom_data_provider._build_ohlc = build_ohlc_with_previous_trade_value
    kiwoom_data_provider.fetch_ohlc = fetch_ohlc_with_previous_trade_value
    kiwoom_data_provider._previous_trade_value_patch_installed = True
