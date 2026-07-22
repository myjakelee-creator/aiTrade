from __future__ import annotations

"""Keep the last verified StockBoard visible while the next display is prepared.

This patch is deliberately outside the realtime collector path.  It reuses the
portable exact-close snapshot, the existing opening-burst cache, and accepted
trade events.  It adds no QAx owner, FID, REST request, WebSocket, thread,
timer, or SSE cadence.
"""

import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number
from realtime_v2.market_session import last_completed_trading_date

PATCH_VERSION = "board_display_continuity_v1"
LAST_GOOD_FILENAME = "portable_board_display_last_good.json"
CANDIDATE_FILENAME = "ohlc_snapshot_candidate.json"
OVERLAY_INTERVAL_SEC = 2.0

_LIVE_TIME_FIELDS = (
    "price_received_at",
    "trade_received_at",
    "received_at",
    "last_trade_event_received_at",
)


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_date(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return _date_digits(payload.get("source_trading_date") or payload.get("trading_date"))


def _content_fingerprint(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    board_values = payload.get("board_values")
    if not isinstance(board_values, dict):
        return ""
    compact: list[list[Any]] = []
    for raw_code in sorted(board_values):
        row = board_values.get(raw_code)
        if not isinstance(row, dict):
            continue
        ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else {}
        compact.append(
            [
                normalize_code(raw_code),
                row.get("price"),
                row.get("change_rate"),
                row.get("trade_value_eok"),
                row.get("prev_trade_value_eok"),
                ohlc.get("open"),
                ohlc.get("high"),
                ohlc.get("low"),
                ohlc.get("close"),
            ]
        )
    encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trusted_payload(
    guard_module,
    payload: Any,
    *,
    expected_date: str | None = None,
    require_verified: bool = True,
) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("portable_policy_version") != guard_module.PORTABLE_POLICY_VERSION:
        return False
    if payload.get("portable_parser_version") != guard_module.PORTABLE_PARSER_VERSION:
        return False
    if require_verified and payload.get("verified") is not True:
        return False
    source_date = _source_date(payload)
    if not source_date or (expected_date and source_date != _date_digits(expected_date)):
        return False
    board_values = payload.get("board_values")
    return isinstance(board_values, dict) and bool(board_values)


def _ensure_guard_state(guard) -> None:
    if getattr(guard, "_display_continuity_ready", False):
        return
    guard._display_continuity_ready = True
    guard._display_last_good_path = guard.snapshot_path.with_name(LAST_GOOD_FILENAME)
    guard._display_candidate_path = guard.snapshot_path.with_name(CANDIDATE_FILENAME)
    guard._display_last_good_payload = _read_json(guard._display_last_good_path)
    guard._display_last_fingerprint = _content_fingerprint(guard._display_last_good_payload)
    guard._display_generation_by_fingerprint: dict[str, int] = {}
    guard._display_next_generation = 0
    guard._display_last_overlay_mono = 0.0
    guard._display_last_overlay_key: tuple[str, str] | None = None
    guard._display_last_counts = (0, 0, 0)


def _remember_verified(guard_module, guard, payload: dict[str, Any]) -> tuple[str, int]:
    _ensure_guard_state(guard)
    if not _trusted_payload(guard_module, payload, require_verified=True):
        return "", int(getattr(guard, "_display_next_generation", 0) or 0)
    fingerprint = _content_fingerprint(payload)
    if not fingerprint:
        return "", int(getattr(guard, "_display_next_generation", 0) or 0)
    generation = guard._display_generation_by_fingerprint.get(fingerprint)
    if generation is None:
        guard._display_next_generation += 1
        generation = guard._display_next_generation
        guard._display_generation_by_fingerprint[fingerprint] = generation
    guard._display_last_good_payload = deepcopy(payload)
    if fingerprint != guard._display_last_fingerprint:
        _atomic_write(guard._display_last_good_path, payload)
        guard._display_last_fingerprint = fingerprint
    return fingerprint, generation


def _positive(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    result = float(number)
    return result if result > 0 else None


def _extract_code(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    candidates: list[Any] = list(args) + list(kwargs.values())
    for candidate in candidates:
        if isinstance(candidate, dict):
            nested = candidate.get("stock") if isinstance(candidate.get("stock"), dict) else {}
            for key in ("stock_code", "code", "display_code", "query_code", "order_code"):
                code = normalize_code(candidate.get(key) or nested.get(key))
                if code:
                    return code
        elif isinstance(candidate, str):
            code = normalize_code(candidate)
            if len(code) == 6 and code.isdigit():
                return code
    return ""


def _timestamp_is_current(value: Any, current_date: str) -> bool:
    return _date_digits(value) == current_date


def _current_live_codes(state, current_date: str) -> set[str]:
    mapping = getattr(state, "_board_display_live_codes_by_date", None)
    result = set(mapping.get(current_date, set())) if isinstance(mapping, dict) else set()
    with state.lock:
        for raw_code, quote in state.quotes.items():
            if not isinstance(quote, dict):
                continue
            if any(_timestamp_is_current(quote.get(key), current_date) for key in _LIVE_TIME_FIELDS):
                code = normalize_code(raw_code)
                if code:
                    result.add(code)
    return result


def _apply_payload(
    guard_module,
    state,
    payload: dict[str, Any],
    *,
    source_date: str,
    current_date: str | None = None,
    hold_only: bool = False,
    generation: int,
) -> tuple[int, int, int]:
    board_values = payload.get("board_values")
    if not isinstance(board_values, dict):
        return 0, 0, 0
    exact_by_code = {
        normalize_code(code): value
        for code, value in board_values.items()
        if normalize_code(code)
        and isinstance(value, dict)
        and value.get("portable_parser_version") == guard_module.PORTABLE_PARSER_VERSION
    }
    previous_ranked = sorted(
        [
            (code, value)
            for code, raw in exact_by_code.items()
            if (value := _positive(raw.get("prev_trade_value_eok"))) is not None
        ],
        key=lambda item: (-item[1], item[0]),
    )
    previous_rank_by_code = {
        code: rank for rank, (code, _value) in enumerate(previous_ranked, start=1)
    }
    live_codes = _current_live_codes(state, current_date or "") if hold_only and current_date else set()
    applied = 0
    held = 0
    live = 0
    with state.lock:
        universe_codes = list(getattr(state, "seed_rank_by_code", {}) or state.quotes)
        for raw_code in universe_codes:
            code = normalize_code(raw_code)
            if not code:
                continue
            if hold_only and code in live_codes:
                live += 1
                continue
            exact = exact_by_code.get(code)
            if not isinstance(exact, dict):
                continue
            price = _positive(exact.get("price"))
            change_rate = to_number(exact.get("change_rate"))
            trade_value = to_number(exact.get("trade_value_eok"))
            ohlc = exact.get("ohlc") if isinstance(exact.get("ohlc"), dict) else None
            exact_date_ok = all(
                _date_digits(exact.get(key)) == source_date
                for key in (
                    "source_trading_date",
                    "price_trading_date",
                    "change_rate_trading_date",
                    "trade_value_trading_date",
                    "ohlc_trading_date",
                )
            )
            if price is None or change_rate is None or trade_value is None or ohlc is None or not exact_date_ok:
                continue
            quote = state._quote(code)
            previous_value = _positive(exact.get("prev_trade_value_eok"))
            quote.update(
                {
                    "price": price,
                    "trade_price": price,
                    "change_rate": round(float(change_rate), 4),
                    "trade_value_eok": round(float(trade_value), 4),
                    "ohlc": deepcopy(ohlc),
                    "day_open": ohlc.get("open"),
                    "day_high": ohlc.get("high"),
                    "day_low": ohlc.get("low"),
                    "day_close": ohlc.get("close"),
                    "prev_trade_value_eok": previous_value,
                    "prev_trade_value_date": exact.get("prev_trade_value_date"),
                    "prev_rank": previous_rank_by_code.get(code),
                    "source_code": "portable_exact_close",
                    "row_source": "portable_exact_close",
                    "source_trading_date": source_date,
                    "price_trading_date": source_date,
                    "change_rate_trading_date": source_date,
                    "trade_value_trading_date": source_date,
                    "ohlc_trading_date": source_date,
                    "market_scope": exact.get("market_scope"),
                    "portable_board_quality": exact.get("quality"),
                    "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
                    "portable_board_generation": generation,
                    "portable_board_applied_at": now_text(),
                    "price_age_sec": None,
                }
            )
            for key in _LIVE_TIME_FIELDS:
                quote.pop(key, None)
            quote.pop("portable_board_missing", None)
            if previous_value is not None:
                state.prev_trade_value_by_code[code] = previous_value
            if previous_rank_by_code.get(code):
                state.prev_rank_by_code[code] = previous_rank_by_code[code]
            applied += 1
            held += 1 if hold_only else 0
    return applied, held, live


def _set_continuity_status(
    state,
    *,
    mode: str,
    current_date: str | None,
    source_date: str | None,
    fingerprint: str | None,
    generation: int,
    applied: int,
    held: int,
    live: int,
) -> None:
    with state.lock:
        state.status.update(
            {
                "board_display_continuity_version": PATCH_VERSION,
                "board_display_continuity_mode": mode,
                "board_display_current_trading_date": current_date or None,
                "board_display_hold_source_trading_date": source_date or None,
                "board_display_content_fingerprint": fingerprint or None,
                "board_portable_generation": generation,
                "board_display_exact_applied_count": applied,
                "board_display_previous_close_hold_count": held,
                "board_display_current_live_count": live,
            }
        )


def _install_guard_continuity(base) -> None:
    from realtime_v2 import worker_board_trading_date_guard as guard_module

    guard_class = guard_module.PortableBoardGuard
    if getattr(guard_class, "_stockboard_display_continuity_installed", False):
        return
    original_apply = guard_class.apply

    def apply(self, state, now=None):
        _ensure_guard_state(self)
        current = now or datetime.now()
        target_date, phase, active = guard_module.board_target_context(current)

        if not active:
            result = original_apply(self, state, current)
            payload = self._load(force=True)
            if result and _trusted_payload(
                guard_module,
                payload,
                expected_date=target_date,
                require_verified=True,
            ):
                fingerprint, generation = _remember_verified(guard_module, self, payload)
                with state.lock:
                    state.status["board_portable_generation"] = generation
                    state.status["board_display_basis"] = "portable_exact_close"
                    state.status["board_source_trading_date"] = target_date
                _set_continuity_status(
                    state,
                    mode="verified_exact_close",
                    current_date=None,
                    source_date=target_date,
                    fingerprint=fingerprint,
                    generation=generation,
                    applied=int(state.status.get("board_exact_row_count") or 0),
                    held=0,
                    live=0,
                )
                return True

            fallback = None
            for candidate in (
                _read_json(self._display_candidate_path),
                self._display_last_good_payload,
                _read_json(self._display_last_good_path),
            ):
                if _trusted_payload(
                    guard_module,
                    candidate,
                    expected_date=target_date,
                    require_verified=False,
                ):
                    fallback = candidate
                    break
            if not isinstance(fallback, dict):
                return result

            verified = fallback.get("verified") is True
            if verified:
                fingerprint, generation = _remember_verified(guard_module, self, fallback)
                basis = "portable_exact_close"
                mode = "verified_last_good_fallback"
            else:
                fingerprint = _content_fingerprint(fallback)
                generation = int(getattr(self, "_display_next_generation", 0) or 0) + 1
                basis = "portable_exact_partial"
                mode = "partial_exact_rebuild"
            applied, _held, _live = _apply_payload(
                guard_module,
                state,
                fallback,
                source_date=target_date,
                generation=generation,
            )
            if applied <= 0:
                return result
            self._status(
                state,
                target_date=target_date,
                phase=phase,
                basis=basis,
                payload=fallback,
                generation=generation,
                exact_count=applied,
                missing_count=max(0, len(getattr(state, "seed_rank_by_code", {}) or {}) - applied),
            )
            _set_continuity_status(
                state,
                mode=mode,
                current_date=None,
                source_date=target_date,
                fingerprint=fingerprint,
                generation=generation,
                applied=applied,
                held=0,
                live=0,
            )
            self._applied_result = True
            return True

        previous_date = _date_digits(last_completed_trading_date(current))
        payload = None
        for candidate in (
            self._display_last_good_payload,
            _read_json(self._display_last_good_path),
            self._load(force=True),
            _read_json(self._display_candidate_path),
        ):
            if _trusted_payload(
                guard_module,
                candidate,
                expected_date=previous_date,
                require_verified=True,
            ):
                payload = candidate
                break
        if not isinstance(payload, dict):
            result = original_apply(self, state, current)
            _set_continuity_status(
                state,
                mode="live_passthrough_no_previous_exact",
                current_date=target_date,
                source_date=None,
                fingerprint=None,
                generation=int(state.status.get("board_portable_generation") or 0),
                applied=0,
                held=0,
                live=0,
            )
            return result

        fingerprint, generation = _remember_verified(guard_module, self, payload)
        overlay_key = (target_date, fingerprint)
        now_mono = time.monotonic()
        if (
            self._display_last_overlay_key != overlay_key
            or now_mono - self._display_last_overlay_mono >= OVERLAY_INTERVAL_SEC
        ):
            applied, held, live = _apply_payload(
                guard_module,
                state,
                payload,
                source_date=previous_date,
                current_date=target_date,
                hold_only=True,
                generation=generation,
            )
            self._display_last_overlay_key = overlay_key
            self._display_last_overlay_mono = now_mono
            self._display_last_counts = (applied, held, live)
        else:
            applied, held, live = self._display_last_counts

        # Keep the cache structural identity on the verified content rather than on
        # the clock phase.  Live rows still update through the existing fast overlay.
        self._status(
            state,
            target_date=target_date,
            phase=phase,
            basis="portable_exact_close",
            payload=payload,
            generation=generation,
            exact_count=applied,
            missing_count=0,
        )
        _set_continuity_status(
            state,
            mode="live_with_previous_close_hold" if held else "live_current_day_ready",
            current_date=target_date,
            source_date=previous_date,
            fingerprint=fingerprint,
            generation=generation,
            applied=applied,
            held=held,
            live=live,
        )
        self._applied_result = True
        return True

    guard_class.apply = apply
    guard_class._stockboard_display_continuity_installed = True

    state_class = getattr(base, "State", None)
    original_init = getattr(state_class, "__init__", None)
    original_apply_trade = getattr(state_class, "_apply_trade", None)
    if state_class is not None and callable(original_init) and not getattr(
        state_class, "_stockboard_display_live_tracking_installed", False
    ):
        def state_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self._board_display_live_codes_by_date: dict[str, set[str]] = {}
            with self.lock:
                self.status["board_display_continuity_version"] = PATCH_VERSION

        state_class.__init__ = state_init

        if callable(original_apply_trade):
            def apply_trade(self, *args, **kwargs):
                with self.lock:
                    before = int(self.status.get("trade_count") or 0)
                result = original_apply_trade(self, *args, **kwargs)
                with self.lock:
                    after = int(self.status.get("trade_count") or 0)
                if after > before:
                    code = _extract_code(args, kwargs)
                    current_date, _phase, active = guard_module.board_target_context()
                    if code and active:
                        mapping = getattr(self, "_board_display_live_codes_by_date", None)
                        if not isinstance(mapping, dict):
                            mapping = {}
                            self._board_display_live_codes_by_date = mapping
                        mapping.setdefault(current_date, set()).add(code)
                return result

            state_class._apply_trade = apply_trade
        state_class._stockboard_display_live_tracking_installed = True


def _install_cache_fallback(base) -> None:
    from realtime_v2 import worker_portable_cache_sync_patch as cache_sync

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_display_cache_fallback_installed", False
    ):
        return
    if not getattr(state_class, "_stockboard_portable_cache_sync_installed", False):
        return
    original_snapshot = state_class.snapshot

    def snapshot(self, limit: int = 300):
        payload = original_snapshot(self, limit)
        if not isinstance(payload, dict) or payload.get("rows"):
            return payload
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        sync_status = str(status.get("portable_board_cache_sync_status") or "")
        if sync_status not in {
            "waiting_for_generation_rebuild",
            "blocked_waiting_valid_snapshot",
        }:
            return payload
        lock = getattr(self, "_opening_burst_cache_lock", None)
        if lock is None:
            return payload
        with lock:
            rows = deepcopy(getattr(self, "_opening_burst_cache_rows", []) or [])
            meta = deepcopy(getattr(self, "_opening_burst_cache_meta", None))
        cached_generation, cached_date, cached_basis = cache_sync._cache_portable_signature(self)
        if not rows or cached_generation <= 0 or cached_basis != "portable_exact_close":
            return payload
        result = dict(meta) if isinstance(meta, dict) else {}
        result["rows"] = rows[: max(1, int(limit or 300))]
        result["row_count"] = len(result["rows"])
        merged_status = result.get("status") if isinstance(result.get("status"), dict) else {}
        merged_status.update(status)
        merged_status.update(
            {
                "portable_board_cache_sync_status": "serving_previous_verified_during_rebuild",
                "portable_board_cache_previous_generation": cached_generation,
                "portable_board_cache_previous_source_trading_date": cached_date or None,
                "portable_board_cache_previous_basis": cached_basis,
                "board_display_continuity_version": PATCH_VERSION,
            }
        )
        result["status"] = merged_status
        with self.lock:
            self.status.update(merged_status)
        return result

    state_class.snapshot = snapshot
    state_class._stockboard_display_cache_fallback_installed = True


def install(base) -> None:
    """Install continuity after the existing portable guard without adding a loop."""

    _install_guard_continuity(base)

    from realtime_v2 import worker_portable_cache_sync_patch as cache_sync

    if not getattr(cache_sync, "_display_continuity_install_hooked", False):
        original_install_after = cache_sync.install_after_opening_cache

        def install_after_opening_cache(target_base) -> None:
            original_install_after(target_base)
            _install_cache_fallback(target_base)

        cache_sync.install_after_opening_cache = install_after_opening_cache
        cache_sync._display_continuity_install_hooked = True

    _install_cache_fallback(base)
