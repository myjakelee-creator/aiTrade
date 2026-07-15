from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number

PATCH_VERSION = "stage2_s1_source_diagnostics_v2"
SELECTION_STATUS_KEYS = (
    "selected_code",
    "active_code",
    "focus_code",
    "s1_code",
    "selected_stock_code",
)
SELECTION_QUOTE_KEYS = (
    "is_selected",
    "selected",
    "is_focus",
    "focus_selected",
)
SOURCE_TIME_KEYS = (
    "execution_strength_source_time",
    "execution_strength_polled_at",
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _latest_strength_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = payload.get("cntr_str_tm")
    if not isinstance(rows, list):
        return None
    candidates = [row for row in rows if isinstance(row, dict)]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: "".join(
            character for character in str(row.get("cntr_tm") or "") if character.isdigit()
        ),
        reverse=True,
    )
    return candidates[0]


def install(base) -> None:
    """Fix Stage 2 S1 recognition and distinguish polling from source changes.

    The UI selection is not always persisted to the legacy selected-code file. The
    updater therefore resolves S1 from, in order: the existing file reader, worker
    status, quote selection flags, and the current trade-value top code as a safe
    fallback. No extra REST request is introduced.

    ka10046 polling time and source row time are recorded separately. When the API
    returns the same source row and value, ``execution_strength_updated_at`` keeps
    the last real source-change time instead of pretending that the metric changed.
    """

    import realtime_v2.worker_rest_live_metrics_patch as module

    updater_class = module.RestLiveMetricUpdater
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        updater_class, "_stockboard_stage2_fix_installed", False
    ):
        return

    original_state_init = state_class.__init__
    original_refresh_selected = updater_class._refresh_selected
    original_apply = updater_class._apply
    original_rows = state_class.rows

    module.PERSIST_KEYS = tuple(dict.fromkeys((*module.PERSIST_KEYS, *SOURCE_TIME_KEYS)))
    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *SOURCE_TIME_KEYS))
    )

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["rest_live_metrics_stage2_fix_installed"] = True
            self.status["rest_live_metrics_stage2_fix_version"] = PATCH_VERSION

    def resolve_selected(self) -> None:
        original_refresh_selected(self)
        selected = normalize_code(getattr(self, "selected_code", ""))
        source = "runtime_file" if selected else ""

        if not selected:
            with self.state.lock:
                status = dict(self.state.status)
                quotes = {
                    normalize_code(code): dict(quote)
                    for code, quote in self.state.quotes.items()
                    if isinstance(quote, dict) and normalize_code(code)
                }
            for key in SELECTION_STATUS_KEYS:
                candidate = normalize_code(status.get(key))
                if candidate:
                    selected = candidate
                    source = f"status:{key}"
                    break

            if not selected:
                for code, quote in quotes.items():
                    if any(bool(quote.get(key)) for key in SELECTION_QUOTE_KEYS):
                        selected = code
                        source = "quote_flag"
                        break

            if not selected and quotes:
                selected = max(
                    quotes.items(),
                    key=lambda item: (
                        _number(item[1].get("trade_value_eok")) or 0.0,
                        -int(item[1].get("seed_rank") or 999999),
                        item[0],
                    ),
                )[0]
                source = "trade_value_top1_fallback"

        self.selected_code = selected
        with self.state.lock:
            self.state.status["rest_live_metrics_selected_code"] = selected or None
            self.state.status["rest_live_metrics_selected_source"] = source or "unresolved"

    def patched_apply(self, code: str, metric: str, payload: dict[str, Any]) -> bool:
        if metric != "strength":
            return original_apply(self, code, metric, payload)

        normalized = normalize_code(code)
        row = _latest_strength_row(payload)
        source_time = str(row.get("cntr_tm") or "").strip() if row else ""
        source_value = _number(row.get("cntr_str")) if row else None
        signature = f"{source_time}|{source_value}"
        previous_signatures = getattr(self, "strength_source_signature_by_code", None)
        if not isinstance(previous_signatures, dict):
            previous_signatures = {}
            self.strength_source_signature_by_code = previous_signatures
        previous_signature = previous_signatures.get(normalized)

        with self.state.lock:
            previous_quote = dict(self.state.quotes.get(normalized) or {})
            previous_daily = dict(self.state.daily_values_by_code.get(normalized) or {})
        previous_updated_at = (
            previous_quote.get("execution_strength_updated_at")
            or previous_daily.get("execution_strength_updated_at")
        )

        applied = original_apply(self, code, metric, payload)
        if not applied:
            return False

        polled_at = now_text()
        changed = previous_signature != signature
        previous_signatures[normalized] = signature
        with self.state.lock:
            quote = self.state.quotes.get(normalized)
            daily = self.state.daily_values_by_code.setdefault(normalized, {})
            targets = [target for target in (quote, daily) if isinstance(target, dict)]
            for target in targets:
                target["execution_strength_source_time"] = source_time or None
                target["execution_strength_polled_at"] = polled_at
                if not changed and previous_updated_at:
                    target["execution_strength_updated_at"] = previous_updated_at

            self.state.status["rest_live_strength_source_time"] = source_time or None
            self.state.status["rest_live_strength_polled_at"] = polled_at
            if changed:
                self.state.status["rest_live_strength_changed_count"] = int(
                    self.state.status.get("rest_live_strength_changed_count") or 0
                ) + 1
                self.state.status["rest_live_strength_last_changed_code"] = normalized
                self.state.status["rest_live_strength_last_changed_at"] = polled_at
            else:
                self.state.status["rest_live_strength_unchanged_count"] = int(
                    self.state.status.get("rest_live_strength_unchanged_count") or 0
                ) + 1
                self.state.status["rest_live_strength_last_unchanged_code"] = normalized
        return True

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        codes = {
            normalize_code(row.get("stock_code"))
            for row in result
            if isinstance(row, dict)
        }
        codes.discard("")
        with self.lock:
            sources = {
                code: {
                    **dict(self.daily_values_by_code.get(code) or {}),
                    **dict(self.quotes.get(code) or {}),
                }
                for code in codes
            }
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            source = sources.get(code, {})
            for key in SOURCE_TIME_KEYS:
                if row.get(key) in (None, "") and source.get(key) not in (None, ""):
                    row[key] = deepcopy(source.get(key))
        return result

    state_class.__init__ = state_init
    updater_class._refresh_selected = resolve_selected
    updater_class._apply = patched_apply
    state_class.rows = rows
    updater_class._stockboard_stage2_fix_installed = True
