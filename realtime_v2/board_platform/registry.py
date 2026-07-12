from __future__ import annotations

from typing import Any

BOARD_DEFINITIONS = (
    {
        "board_id": "stockboard",
        "label": "StockBoard",
        "short_label": "종목",
        "url": "/",
        "enabled": True,
        "description": "종목 단위 실시간 관찰·후보 선발",
    },
    {
        "board_id": "themeboard",
        "label": "ThemeBoard",
        "short_label": "테마",
        "url": "/theme",
        "enabled": True,
        "description": "테마 단위 돈쏠림·주도주 관찰",
    },
    {
        "board_id": "strategyboard",
        "label": "StrategyBoard",
        "short_label": "전략",
        "url": "/strategy",
        "enabled": False,
        "description": "장초반·종가베팅 전략 보드 · 준비중",
    },
    {
        "board_id": "boards",
        "label": "Boards",
        "short_label": "전체",
        "url": "/boards",
        "enabled": True,
        "description": "보드 상태와 이동을 위한 경량 허브",
    },
)


def _service_status(server: Any, attr: str) -> dict[str, Any]:
    service = getattr(server, attr, None)
    if service is None or not hasattr(service, "status"):
        return {}
    try:
        payload = service.status()
        return payload if isinstance(payload, dict) else {}
    except Exception as error:
        return {"state": "ERROR", "last_error": f"{type(error).__name__}: {error}"}


def registry_payload(server: Any) -> dict[str, Any]:
    stock = _service_status(server, "stockboard_snapshot_cache")
    theme = _service_status(server, "theme_cache_service")
    performance = _service_status(server, "board_performance_service")
    result = []
    for item in BOARD_DEFINITIONS:
        row = dict(item)
        board_id = row["board_id"]
        if board_id == "stockboard":
            row.update(
                {
                    "state": stock.get("state") or "READY",
                    "clients": stock.get("clients") or 0,
                    "cache_version": stock.get("cache_version") or 0,
                    "last_updated_at": stock.get("last_updated_at"),
                    "last_error": stock.get("last_error"),
                }
            )
        elif board_id == "themeboard":
            row.update(
                {
                    "state": theme.get("state") or ("READY" if theme.get("ok") else "WAIT"),
                    "clients": theme.get("theme_clients") or 0,
                    "cache_version": theme.get("cache_version") or 0,
                    "last_updated_at": theme.get("last_updated_at"),
                    "last_error": theme.get("last_error"),
                }
            )
        elif board_id == "strategyboard":
            row.update(
                {
                    "state": "DISABLED",
                    "clients": 0,
                    "cache_version": 0,
                    "last_updated_at": None,
                    "last_error": None,
                }
            )
        else:
            row.update(
                {
                    "state": performance.get("overall_state") or "READY",
                    "clients": 0,
                    "cache_version": performance.get("cache_version") or 0,
                    "last_updated_at": performance.get("last_updated_at"),
                    "last_error": performance.get("last_error"),
                }
            )
        result.append(row)
    return {
        "schema_version": 1,
        "source": "board_platform_registry",
        "boards": result,
    }
