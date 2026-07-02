import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


KST = timezone(timedelta(hours=9))
# ka90004 netprps_prica is provisionally treated as KRW millions: 100 million KRW per eok.
PROGRAM_NET_EOK_DIVISOR_TEXT = os.getenv("KIWOOM_PROGRAM_NET_EOK_DIVISOR", "100")
REQUEST_SLEEP_SEC_TEXT = os.getenv("KIWOOM_REQUEST_SLEEP_SEC", "0.25")

OUTPUT_KEYS = (
    "stock_code", "rank", "original_rank", "prev_rank", "grade", "stock_name",
    "price", "change_rate", "trade_value_eok", "ohlc", "bid_ask_ratio",
    "strength_1m", "strength_day", "foreign_sum", "foreign_investor_net",
    "foreign_display_label", "foreign_display_value", "foreign_display_source",
    "program_net", "big_hand", "momentum",
)

CANDIDATE_SCORE_MODEL = {
    "id": "TVRANK_A_V03_TEMP",
    "name": "거래대금 순위 중심",
    "version": "TVRANK_A_V03_TEMP",
    "items": [
        {"key": "top50_trade_value_rank", "stage": "top50", "max_score": 30, "enabled": True},
        {"key": "top50_rank_gap", "stage": "top50", "max_score": 30, "enabled": True},
        {"key": "top50_foreign_sum", "stage": "top50", "max_score": 0, "enabled": False},
        {"key": "top50_program_net", "stage": "top50", "max_score": 40, "enabled": True},
        {"key": "top20_trade_value_ratio", "stage": "top20", "max_score": 50, "enabled": True},
        {"key": "top20_large_trade_net_count", "stage": "top20", "max_score": 0, "enabled": False, "diagnostic_only": True},
        {"key": "top5_one_min_strength_growth", "stage": "top5", "max_score": 30, "enabled": True},
        {"key": "top5_realtime_strength_rank", "stage": "top5", "max_score": 20, "enabled": True},
        {"key": "top5_one_min_net_buy_value_growth", "stage": "top5", "max_score": 50, "enabled": True},
    ],
}

def prepare_display_rows(top100_rows, tradable_codes, program_net_by_code):
    filtered_rows = [row for row in top100_rows if row["stock_code"] in tradable_codes]
    for display_rank, row in enumerate(filtered_rows, start=1):
        row["rank"] = display_rank
        row["displayed_rank"] = display_rank
        row["program_net"] = program_net_by_code.get(row["stock_code"])
    return filtered_rows


def build_top100_filter_report(raw_rows, displayed_rows, tradable_codes):
    displayed_rank_by_code = {
        row.get("stock_code"): row.get("rank")
        for row in displayed_rows
        if row.get("stock_code")
    }
    report_rows = []
    for row in raw_rows:
        stock_code = row.get("stock_code")
        in_tradable_master = stock_code in tradable_codes
        filter_passed = stock_code in displayed_rank_by_code
        report_rows.append(
            {
                "original_rank": row.get("original_rank"),
                "displayed_rank": displayed_rank_by_code.get(stock_code),
                "stock_code": stock_code,
                "stock_name": row.get("stock_name"),
                "price": row.get("price"),
                "change_rate": row.get("change_rate"),
                "trade_value_eok": row.get("trade_value_eok"),
                "filter_passed": filter_passed,
                "filter_reason": (
                    "passed" if filter_passed else "not_in_tradable_master"
                ),
                "in_tradable_master": in_tradable_master,
            }
        )
    return report_rows


def _first(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number_or_none(value):
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
    return float(number)


def _clamp(number, minimum, maximum):
    return max(minimum, min(maximum, number))


def _candidate_score_max(model=None):
    model = model or CANDIDATE_SCORE_MODEL
    return sum(
        _number_or_none(item.get("max_score")) or 0
        for item in model.get("items", [])
    )


def _item_max_score(key, model=None):
    model = model or CANDIDATE_SCORE_MODEL
    for item in model.get("items", []):
        if item.get("key") == key:
            return _number_or_none(item.get("max_score")) or 0
    return 0


def _enabled_model_items(model=None):
    model = model or CANDIDATE_SCORE_MODEL
    enabled_items = []
    for item in model.get("items", []):
        max_score = _number_or_none(item.get("max_score")) or 0
        if item.get("enabled", True) and max_score > 0:
            next_item = dict(item)
            next_item["max_score"] = max_score
            enabled_items.append(next_item)
    return enabled_items


def _component_points(key, stage, max_score, score, source, reason=None):
    if score is None:
        points = 0
        status = "missing"
        reason = reason or "required_data_missing"
    else:
        points = round(_clamp(score, 0, max_score), 2)
        status = "ok"
        reason = reason or "data_available"
    return {
        "key": key,
        "stage": stage,
        "enabled": True,
        "diagnostic_only": False,
        "weight": max_score,
        "earned_points": points,
        "points": points,
        "possible_points": max_score,
        "source": source,
        "status": status,
        "coverage_status": status,
        "reason": reason,
    }


def _disabled_component(key, stage, source, max_score=0, reason=None, diagnostic_only=False):
    status = "diagnostic_only" if diagnostic_only else "disabled"
    return {
        "key": key,
        "stage": stage,
        "enabled": False,
        "diagnostic_only": diagnostic_only,
        "weight": max_score,
        "earned_points": 0,
        "points": 0,
        "possible_points": 0,
        "source": source,
        "status": status,
        "coverage_status": status,
        "reason": reason or ("diagnostic_only_weight_0" if diagnostic_only else "disabled_by_model_policy"),
    }


def _sum_component_points(components, stage):
    return round(
        sum(item["points"] for item in components if item.get("stage") == stage),
        2,
    )


def _sum_component_possible(components, stage):
    return round(
        sum(
            item["possible_points"]
            for item in components
            if item.get("stage") == stage and item.get("enabled")
        ),
        2,
    )


def _current_rank(row):
    return _number_or_none(_first(row, "rank", "displayed_rank"))


def _rank_score(current_rank, max_score):
    if current_rank is None or max_score <= 0:
        return None
    # Rank 1 is full score and rank 100+ approaches zero.
    return _clamp((100 - current_rank) / 99 * max_score, 0, max_score)


def _rank_gap(row, current_rank):
    direct_gap = _number_or_none(_first(row, "rank_diff"))
    if direct_gap is not None:
        return direct_gap
    prev_rank = _number_or_none(_first(row, "prev_rank"))
    if prev_rank is None or current_rank is None:
        return None
    return prev_rank - current_rank


def _rank_gap_score(rank_gap, max_score):
    if rank_gap is None or max_score <= 0:
        return None
    if rank_gap <= 0:
        return 0
    return _clamp(rank_gap, 0, max_score)


def _candidate_grade(score):
    if score is None:
        return (None, "")
    score = _clamp(score, 0, 100)
    if score >= 91:
        return ("A", "a")
    if score >= 81:
        return ("B", "b")
    if score >= 71:
        return ("C", "c")
    if score >= 61:
        return ("D", "d")
    return ("F", "f")


def _grade_text(score):
    grade, _grade_class = _candidate_grade(score)
    if grade is None or score is None:
        return "-"
    return f"{grade}{int(round(_clamp(score, 0, 100)))}"


def _rank_window_score(current_rank, max_rank, max_score):
    if current_rank is None or max_score <= 0:
        return None
    if max_rank <= 1:
        return max_score
    score = (max_rank - current_rank + 1) / max_rank * max_score
    return round(_clamp(score, 0, max_score), 2)


def _positive_scaled_score(value, max_observed, max_score):
    if value is None or max_score <= 0:
        return None
    if max_observed is None or max_observed <= 0:
        return 0
    return round(_clamp(value, 0, max_observed) / max_observed * max_score, 2)


def _ranked_metric_score(value, values, max_score, reverse=True):
    if value is None or max_score <= 0:
        return None
    ranked_values = sorted(
        {item for item in values if item is not None},
        reverse=reverse,
    )
    if not ranked_values:
        return None
    try:
        rank_index = ranked_values.index(value)
    except ValueError:
        return None
    if len(ranked_values) == 1:
        return max_score
    score = (len(ranked_values) - rank_index) / len(ranked_values) * max_score
    return round(_clamp(score, 0, max_score), 2)


def _trade_value_ratio(row):
    current = _number_or_none(_first(row, "trade_value_eok"))
    previous = _number_or_none(
        _first(
            row,
            "prev_trade_value_eok",
            "previous_trade_value_eok",
            "yesterday_trade_value_eok",
        )
    )
    if current is None or previous in (None, 0):
        return None
    return current / previous


def _pool_stage(funnel_rank):
    if funnel_rank is None:
        return "top300"
    if funnel_rank <= 5:
        return "top5"
    if funnel_rank <= 20:
        return "top20"
    if funnel_rank <= 50:
        return "top50"
    return "top300"


def _trend_status(row):
    price = _number_or_none(_first(row, "price", "realtime_price"))
    ohlc = _first(row, "realtime_ohlc", "ohlc")
    open_price = None
    if isinstance(ohlc, dict):
        open_price = _number_or_none(ohlc.get("open"))
    if price is None or open_price is None:
        return (None, "시가 원천 부족")
    if price > open_price:
        return (True, "현재가가 시가 위")
    return (False, "현재가가 시가 아래")


def _candidate_status(score, trend_ok):
    if score is None or score < 40:
        return "WEAK"
    if trend_ok is True and score >= 60:
        return "READY"
    return "WATCH"


def detect_price_limit_state(row):
    price = _number_or_none(_first(row, "price", "realtime_price"))
    change_rate = _number_or_none(_first(row, "change_rate", "realtime_change_rate"))
    upper_limit = _number_or_none(
        _first(row, "upper_limit_price", "high_limit", "up_limit_price")
    )
    lower_limit = _number_or_none(
        _first(row, "lower_limit_price", "low_limit", "down_limit_price")
    )

    if price is not None and upper_limit is not None and price >= upper_limit:
        return {
            "price_limit_state": "upper",
            "price_limit_reason": "상한가",
            "price_limit_source": "limit_price",
        }
    if price is not None and lower_limit is not None and price <= lower_limit:
        return {
            "price_limit_state": "lower",
            "price_limit_reason": "하한가",
            "price_limit_source": "limit_price",
        }
    if change_rate is not None and change_rate >= 29.5:
        return {
            "price_limit_state": "upper",
            "price_limit_reason": "상한가 추정",
            "price_limit_source": "change_rate_threshold",
        }
    if change_rate is not None and change_rate <= -29.5:
        return {
            "price_limit_state": "lower",
            "price_limit_reason": "하한가 추정",
            "price_limit_source": "change_rate_threshold",
        }
    return {
        "price_limit_state": "none",
        "price_limit_reason": None,
        "price_limit_source": (
            "unavailable"
            if upper_limit is None and lower_limit is None
            else "limit_price"
        ),
    }


def enrich_limit_state_fields(rows):
    for row in rows:
        row.update(detect_price_limit_state(row))
    return rows


def _score_text(number):
    if number is None:
        return None
    if number == int(number):
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def _candidate_text(row, current_rank, rank_gap, trend_ok):
    reason_parts = []
    momentum_parts = ["거래대금상위"]
    if current_rank is not None:
        reason_parts.append(f"거래대금순위 {int(current_rank)}위")
    if rank_gap is None:
        reason_parts.append("전일순위부족")
        momentum_parts.append("전일순위부족")
    elif rank_gap > 0:
        reason_parts.append(f"전일대비 {int(rank_gap)}계단 상승")
        momentum_parts.append("순위급상승" if rank_gap >= 20 else "순위개선")
    else:
        reason_parts.append("전일대비 순위개선 없음")
    if trend_ok is True:
        momentum_parts.append("시가위")
    elif trend_ok is False:
        momentum_parts.append("시가아래")
    else:
        momentum_parts.append("시가부족")
    return " + ".join(momentum_parts), " + ".join(reason_parts)


def enrich_candidate_fields(rows, model=None):
    model = model or CANDIDATE_SCORE_MODEL
    enrich_limit_state_fields(rows)
    enriched_rows = []
    model_items = model.get("items", [])
    metric_rows = []

    for row in rows:
        current_rank = _current_rank(row)
        rank_gap = _rank_gap(row, current_rank)
        metrics = {
            "current_rank": current_rank,
            "rank_gap": rank_gap,
            "trade_value_ratio": _trade_value_ratio(row),
            "program_net": _number_or_none(_first(row, "program_net")),
            "foreign_sum": _number_or_none(
                _first(row, "foreign_sum", "foreign_display_value")
            ),
            "large_trade_net_count": _number_or_none(
                _first(row, "large_trade_net_count")
            ),
            "one_min_strength_growth": _number_or_none(
                _first(row, "one_min_strength_growth_rate", "one_min_strength_delta")
            ),
            "realtime_strength": _number_or_none(
                _first(row, "realtime_strength", "execution_strength")
            ),
            "one_min_net_buy_value_growth": _number_or_none(
                _first(
                    row,
                    "one_min_net_buy_value_delta_eok",
                    "one_min_net_buy_value_eok",
                )
            ),
        }
        metric_rows.append((row, metrics))

    max_program_net = max(
        [metrics["program_net"] for _row, metrics in metric_rows if metrics["program_net"] is not None and metrics["program_net"] > 0],
        default=None,
    )
    max_trade_value_ratio = max(
        [metrics["trade_value_ratio"] for _row, metrics in metric_rows if metrics["trade_value_ratio"] is not None and metrics["trade_value_ratio"] > 0],
        default=None,
    )
    max_one_min_strength_growth = max(
        [metrics["one_min_strength_growth"] for _row, metrics in metric_rows if metrics["one_min_strength_growth"] is not None and metrics["one_min_strength_growth"] > 0],
        default=None,
    )
    realtime_strength_values = [
        metrics["realtime_strength"]
        for _row, metrics in metric_rows
        if metrics["realtime_strength"] is not None
    ]
    max_one_min_net_buy_value_growth = max(
        [metrics["one_min_net_buy_value_growth"] for _row, metrics in metric_rows if metrics["one_min_net_buy_value_growth"] is not None and metrics["one_min_net_buy_value_growth"] > 0],
        default=None,
    )

    for row, metrics in metric_rows:
        legacy_grade = _first(row, "grade", "legacy_grade")
        current_rank = metrics["current_rank"]
        rank_gap = metrics["rank_gap"]
        score_sources = {
            "top300": "api_top100_trade_value_pool",
            "top50_trade_value_rank": "rank/displayed_rank",
            "top50_rank_gap": "rank_diff_or_prev_rank",
            "top50_foreign_sum": "foreign_sum_diagnostic_only_weight_0",
            "top50_program_net": "program_net",
            "top20_trade_value_ratio": "trade_value_eok_vs_prev_trade_value_eok",
            "top20_large_trade_net_count": "large_trade_net_count_diagnostic_only_weight_0",
            "large_trade_net_sum_eok": "tooltip_diagnostic_only",
            "top5_one_min_strength_growth": "one_min_strength_growth_rate_or_delta",
            "top5_realtime_strength_rank": "realtime_strength",
            "top5_one_min_net_buy_value_growth": "one_min_net_buy_value_delta_eok",
        }
        score_reasons = {
            "top50_trade_value_rank": "rank based score from current displayed rank",
            "top50_rank_gap": "designed for previous rank snapshot or TR based rank comparison",
            "top50_foreign_sum": "foreign sum remains diagnostic until regular-session validation",
            "top50_program_net": "program net contributes only when program_net data is present",
            "top20_trade_value_ratio": "pending when previous-day trade value is unavailable",
            "top20_large_trade_net_count": "large trade collection quality not verified; threshold remains 50000000 KRW per fill",
            "top5_one_min_strength_growth": "pending when one-minute strength growth data is unavailable",
            "top5_realtime_strength_rank": "pending when realtime strength data is unavailable",
            "top5_one_min_net_buy_value_growth": "pending when one-minute net-buy value growth data is unavailable",
        }
        score_lookup = {
            "top50_trade_value_rank": _rank_window_score(current_rank, 300, 30),
            "top50_rank_gap": _rank_gap_score(rank_gap, 30),
            "top50_foreign_sum": None,
            "top50_program_net": _positive_scaled_score(
                metrics["program_net"], max_program_net, 40
            ),
            "top20_trade_value_ratio": _positive_scaled_score(
                metrics["trade_value_ratio"], max_trade_value_ratio, 50
            ),
            "top20_large_trade_net_count": None,
            "top5_one_min_strength_growth": _positive_scaled_score(
                metrics["one_min_strength_growth"], max_one_min_strength_growth, 30
            ),
            "top5_realtime_strength_rank": _ranked_metric_score(
                metrics["realtime_strength"], realtime_strength_values, 20
            ),
            "top5_one_min_net_buy_value_growth": _positive_scaled_score(
                metrics["one_min_net_buy_value_growth"],
                max_one_min_net_buy_value_growth,
                50,
            ),
        }
        score_components = []
        for item in model_items:
            key = item.get("key")
            stage = item.get("stage")
            max_score = _number_or_none(item.get("max_score")) or 0
            if not item.get("enabled", True) or max_score <= 0:
                score_components.append(
                    _disabled_component(
                        key,
                        stage,
                        score_sources.get(key),
                        max_score=max_score,
                        reason=score_reasons.get(key),
                        diagnostic_only=bool(item.get("diagnostic_only") or max_score == 0),
                    )
                )
                continue
            score_components.append(
                _component_points(
                    key,
                    stage,
                    max_score,
                    score_lookup.get(key),
                    score_sources.get(key),
                    reason=score_reasons.get(key),
                )
            )
        score_top50 = _sum_component_points(score_components, "top50")
        score_top20 = _sum_component_points(score_components, "top20")
        score_top5 = _sum_component_points(score_components, "top5")
        possible_top50 = _sum_component_possible(score_components, "top50")
        possible_top20 = _sum_component_possible(score_components, "top20")
        possible_top5 = _sum_component_possible(score_components, "top5")
        score_total_points = round(
            sum(item["points"] for item in score_components), 2
        )
        score_possible_points = round(
            sum(
                item["possible_points"]
                for item in score_components
                if item.get("enabled")
            ),
            2,
        )
        score = (
            int(round(score_total_points / score_possible_points * 100))
            if score_possible_points > 0
            else None
        )
        grade, grade_class = _candidate_grade(score)
        grade_text = _grade_text(score)
        trend_ok, trend_reason = _trend_status(row)
        status = _candidate_status(score, trend_ok)
        momentum, reason = _candidate_text(row, current_rank, rank_gap, trend_ok)
        component_statuses = {
            item["key"]: item["status"] for item in score_components
        }
        score_status = (
            "partial"
            if any(item["status"] == "missing" for item in score_components)
            else "ok"
        )

        row["candidate_score_raw"] = score_total_points
        row["candidate_score_max"] = score_possible_points
        row["candidate_score"] = score
        row["score_top50"] = score_top50
        row["score_top20"] = score_top20
        row["score_top5"] = score_top5
        row["score_total"] = score_total_points
        row["score_total_points"] = score_total_points
        row["score_possible_points"] = score_possible_points
        row["score_percent"] = score
        row["grade_letter"] = grade
        row["legacy_grade"] = legacy_grade
        row["candidate_grade"] = grade
        row["candidate_grade_text"] = grade_text
        row["candidate_grade_class"] = grade_class
        row["display_grade_source"] = "candidate_grade_text"
        row["grade_fallback_used"] = False
        row["candidate_reason"] = reason
        row["candidate_reason_tokens"] = [
            token.strip() for token in reason.split("+") if token.strip()
        ]
        row["trend_ok"] = trend_ok
        row["trend_reason"] = trend_reason
        row["momentum"] = momentum
        row["candidate_status"] = status
        row["candidate_model_id"] = model.get("id") or model.get("version")
        row["candidate_model_name"] = model.get("name")
        row["candidate_score_version"] = model.get("version")
        enabled_components = [
            item
            for item in score_components
            if item.get("enabled") and not item.get("diagnostic_only")
        ]
        row["candidate_score_coverage"] = (
            round(
                len(
                    [
                        item
                        for item in enabled_components
                        if item.get("status") != "missing"
                    ]
                )
                / len(enabled_components),
                2,
            )
            if enabled_components
            else None
        )
        row["candidate_score_items"] = {
            "top50": {
                item["key"]: item["points"]
                for item in score_components
                if item.get("stage") == "top50"
            },
            "top20": {
                item["key"]: item["points"]
                for item in score_components
                if item.get("stage") == "top20"
            },
            "top5": {
                item["key"]: item["points"]
                for item in score_components
                if item.get("stage") == "top5"
            },
        }
        row["score_breakdown"] = {
            "top50": {
                "score": score_top50,
                "possible_points": possible_top50,
                "items": [
                    item for item in score_components if item.get("stage") == "top50"
                ],
            },
            "top20": {
                "score": score_top20,
                "possible_points": possible_top20,
                "items": [
                    item for item in score_components if item.get("stage") == "top20"
                ],
            },
            "top5": {
                "score": score_top5,
                "possible_points": possible_top5,
                "items": [
                    item for item in score_components if item.get("stage") == "top5"
                ],
            },
            "total": {
                "score": score_total_points,
                "possible_points": score_possible_points,
                "percent": score,
                "formula": "round(score_total_points / score_possible_points * 100)",
            },
            "component_status": component_statuses,
        }
        row["score_breakdown"]["diagnostics"] = {
            "foreign_sum": {
                "value": metrics["foreign_sum"],
                "source": score_sources["top50_foreign_sum"],
                "status": component_statuses.get("top50_foreign_sum"),
                "reason": score_reasons["top50_foreign_sum"],
                "scoring_weight": 0,
            },
            "large_trade": {
                "large_trade_net_count": metrics["large_trade_net_count"],
                "large_trade_net_sum_eok": _number_or_none(_first(row, "large_trade_net_sum_eok")),
                "threshold_krw": _number_or_none(_first(row, "large_trade_threshold_krw")) or 50000000,
                "source": score_sources["top20_large_trade_net_count"],
                "status": component_statuses.get("top20_large_trade_net_count"),
                "reason": score_reasons["top20_large_trade_net_count"],
                "scoring_weight": 0,
            },
        }
        row["score_sources"] = score_sources
        row["score_status"] = score_status
        row["is_candidate"] = False
        row["candidate_rank"] = None
        row["pool_stage"] = None
        row["pool_rank"] = None
        row["funnel_rank"] = None
        enriched_rows.append(row)

    top50_rows = sorted(
        enriched_rows,
        key=lambda row: (
            -(_number_or_none(row.get("score_top50")) or -1),
            _current_rank(row) or float("inf"),
        ),
    )[:50]
    top20_rows = sorted(
        top50_rows,
        key=lambda row: (
            -(_number_or_none(row.get("score_top20")) or -1),
            -(_number_or_none(row.get("score_top50")) or -1),
            _current_rank(row) or float("inf"),
        ),
    )[:20]
    candidate_rows = sorted(
        top20_rows,
        key=lambda row: (
            -(_number_or_none(row.get("score_top5")) or -1),
            -(_number_or_none(row.get("score_total")) or -1),
            _current_rank(row) or float("inf"),
        ),
    )[:5]
    ranked_rows = candidate_rows + [
        row for row in top20_rows if row not in candidate_rows
    ] + [
        row for row in top50_rows if row not in top20_rows
    ] + [
        item for item in sorted(
            enriched_rows,
            key=lambda item: _current_rank(item) or float("inf"),
        )
        if item not in top50_rows
    ]

    for funnel_rank, row in enumerate(ranked_rows, start=1):
        row["funnel_rank"] = funnel_rank
        row["pool_rank"] = funnel_rank
        row["pool_stage"] = _pool_stage(funnel_rank)

    for candidate_rank, row in enumerate(candidate_rows, start=1):
        row["is_candidate"] = True
        row["candidate_rank"] = candidate_rank
    return enriched_rows


def _clean_number(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return value
    return int(number) if number == number.to_integral() else float(number)


def _stock_code(value):
    if value in (None, ""):
        return None
    code = "".join(str(value).split()).upper()
    code = code.split("_", 1)[0]
    if code.startswith("A"):
        code = code[1:]
    if not code.isdigit():
        return None
    code = code.zfill(6)
    return code if len(code) == 6 else None


def normalize_kiwoom_price(value):
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    normalized = Decimal(str(abs(number)))
    return int(normalized) if normalized == normalized.to_integral() else float(normalized)


def _absolute_number(value):
    return normalize_kiwoom_price(value)


def _trade_value_eok(value):
    """ka10032 거래대금(백만원)을 억원 단위로 변환한다."""
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    eok = Decimal(str(number)) / Decimal("100")
    return int(eok) if eok == eok.to_integral() else float(eok)


def _program_net_eok_divisor():
    try:
        divisor = Decimal(PROGRAM_NET_EOK_DIVISOR_TEXT)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_PROGRAM_NET_EOK_DIVISOR must be a positive number"
        ) from error
    if not divisor.is_finite() or divisor <= 0:
        raise RuntimeError("KIWOOM_PROGRAM_NET_EOK_DIVISOR must be a positive number")
    return divisor


def _program_net_eok(value, divisor):
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    eok = Decimal(str(number)) / divisor
    return int(eok) if eok == eok.to_integral() else float(eok)


def _query_date():
    query_date = os.getenv("KIWOOM_QUERY_DATE")
    if query_date:
        query_date = query_date.strip()
    else:
        query_date = datetime.now(KST).strftime("%Y%m%d")
    if len(query_date) != 8 or not query_date.isdigit():
        raise RuntimeError("KIWOOM_QUERY_DATE must use YYYYMMDD format")
    try:
        datetime.strptime(query_date, "%Y%m%d")
    except ValueError as error:
        raise RuntimeError("KIWOOM_QUERY_DATE must be a valid YYYYMMDD date") from error
    return query_date


def _program_net_enabled():
    return os.getenv("KIWOOM_PROGRAM_NET_ENABLED", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _request_sleep_sec():
    try:
        seconds = Decimal(REQUEST_SLEEP_SEC_TEXT)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_REQUEST_SLEEP_SEC must be a non-negative number"
        ) from error
    if not seconds.is_finite() or seconds < 0:
        raise RuntimeError("KIWOOM_REQUEST_SLEEP_SEC must be a non-negative number")
    return float(seconds)


def _normalize_row(row):
    raw_stock_code = _first(row, "stk_cd", "stock_code", "종목코드")
    normalized = dict.fromkeys(OUTPUT_KEYS)
    normalized.update(
        {
            "stock_code": _stock_code(raw_stock_code),
            "raw_stock_code": raw_stock_code,
            "source_stock_code": raw_stock_code,
            "rank": _clean_number(_first(row, "now_rank", "rank", "현재순위")),
            "prev_rank": _clean_number(_first(row, "pred_rank", "prev_rank", "전일순위")),
            "stock_name": _first(row, "stk_nm", "stock_name", "종목명"),
            "price": _absolute_number(_first(row, "cur_prc", "price", "현재가")),
            "change_rate": _clean_number(_first(row, "flu_rt", "change_rate", "등락률")),
            "trade_value_eok": _trade_value_eok(
                _first(row, "trde_prica", "trade_value", "거래대금")
            ),
            "foreign_investor_net": _clean_number(
                _first(row, "foreign_investor_net")
            ),
            "foreign_display_label": _first(row, "foreign_display_label"),
            "foreign_display_value": _clean_number(
                _first(row, "foreign_display_value")
            ),
            "foreign_display_source": _first(row, "foreign_display_source"),
        }
    )
    normalized["original_rank"] = normalized["rank"]
    return normalized


def _required_market_number(response, field, market_name):
    value = _clean_number(response.get(field))
    if not isinstance(value, (int, float)):
        raise RuntimeError(
            f"ka20001 {market_name} missing numeric field {field}: {response.get(field)!r}"
        )
    return value


def _required_market_count(response, field, market_name):
    value = _required_market_number(response, field, market_name)
    if value < 0 or int(value) != value:
        raise RuntimeError(
            f"ka20001 {market_name} invalid count field {field}: {value!r}"
        )
    return int(value)


def _market_flow_number(value, field, market_name):
    text = str(value).strip().replace(",", "")
    if text.startswith("--"):
        text = "-" + text[2:]
    number = _clean_number(text)
    if not isinstance(number, (int, float)):
        raise RuntimeError(
            f"{market_name} missing numeric market flow field {field}: {value!r}"
        )
    return number


def _million_to_eok(value, field, market_name):
    number = _market_flow_number(value, field, market_name)
    eok = Decimal(str(number)) / Decimal("100")
    return int(eok) if eok == eok.to_integral() else float(eok)


def _recent_dates(query_date, days=7):
    date = datetime.strptime(query_date, "%Y%m%d")
    return [
        (date - timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(days)
    ]


def _ohlc_price(value):
    return normalize_kiwoom_price(value)


def _daily_row_date(row):
    value = _first(row, "date", "dt", "일자")
    if value in (None, ""):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits if len(digits) == 8 else None


def _select_daily_rows(rows, query_date):
    eligible_rows = sorted(
        (
            (row_date, row)
            for row in rows
            if isinstance(row, dict)
            and (row_date := _daily_row_date(row))
            and row_date <= query_date
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not eligible_rows:
        return None, None

    current_date, current_row = eligible_rows[0]
    previous_row = next(
        (
            row
            for row_date, row in eligible_rows[1:]
            if row_date < current_date
        ),
        None,
    )
    return current_row, previous_row


def _build_ohlc(current_row, previous_row):
    if not current_row or not previous_row:
        return None, None

    prices = {
        "open": _ohlc_price(_first(current_row, "open_pric", "open")),
        "high": _ohlc_price(_first(current_row, "high_pric", "high")),
        "low": _ohlc_price(_first(current_row, "low_pric", "low")),
        "close": _ohlc_price(_first(current_row, "close_pric", "close")),
        "prev_high": _ohlc_price(_first(previous_row, "high_pric", "high")),
        "prev_close": _ohlc_price(_first(previous_row, "close_pric", "close")),
        "prev_low": _ohlc_price(_first(previous_row, "low_pric", "low")),
    }
    if any(value is None for value in prices.values()):
        return None, None
    if prices["high"] < prices["low"] or prices["prev_high"] < prices["prev_low"]:
        return None, None

    amount_million = _clean_number(_first(current_row, "amt_mn", "trade_value_mn"))
    trade_quantity = _clean_number(_first(current_row, "trde_qty", "trade_quantity"))
    vwap_candidate = None
    if (
        isinstance(amount_million, (int, float))
        and isinstance(trade_quantity, (int, float))
        and trade_quantity > 0
    ):
        vwap_decimal = (
            Decimal(str(amount_million)) * Decimal("1000000")
        ) / Decimal(str(trade_quantity))
        vwap_candidate = normalize_kiwoom_price(vwap_decimal)

    vwap = (
        round(vwap_candidate, 2)
        if vwap_candidate is not None
        and prices["low"] <= vwap_candidate <= prices["high"]
        else None
    )
    ohlc = {
        "open": prices["open"],
        "high": prices["high"],
        "low": prices["low"],
        "close": prices["close"],
        "vwap": vwap,
        "prev_high": prices["prev_high"],
        "prev_close": prices["prev_close"],
        "prev_low": prices["prev_low"],
        "trading_date": _daily_row_date(current_row),
    }
    return ohlc, {
        "amt_mn": amount_million,
        "trde_qty": trade_quantity,
        "calculated_vwap": round(vwap_candidate, 2)
        if vwap_candidate is not None
        else None,
        "accepted_vwap": vwap,
    }


def _ohlc_row_sample(row):
    if not row:
        return None
    return {
        "date": _first(row, "date", "dt", "일자"),
        "open_pric": _first(row, "open_pric", "open"),
        "high_pric": _first(row, "high_pric", "high"),
        "low_pric": _first(row, "low_pric", "low"),
        "close_pric": _first(row, "close_pric", "close"),
        "amt_mn": _first(row, "amt_mn", "trade_value_mn"),
        "trde_qty": _first(row, "trde_qty", "trade_quantity"),
    }
