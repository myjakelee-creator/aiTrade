from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.after_close_theme_recovery import (
    ThemeAfterCloseRecoveryPublisher,
    _format_eok,
)


class FakeThemeService:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache_version = 4
        self.snapshot = {
            "cache_version": 4,
            "status": {"display_basis": "LAST_CLOSE"},
            "summary": {},
            "themes": [
                {
                    "theme_id": "T",
                    "theme_name": "테스트",
                    "inflow_1m_text": "-",
                    "inflow_5m_text": "-",
                }
            ],
        }
        self.snapshot_bytes = self.encode(self.snapshot)
        self.last_payload_bytes = len(self.snapshot_bytes)
        self.last_updated_at = None
        self.last_source_version = ("old",)
        self.persist_count = 0

    @staticmethod
    def encode(payload):
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _persist_current(self, force=False):
        self.persist_count += int(bool(force))
        return True


def publisher_for_test():
    publisher = ThemeAfterCloseRecoveryPublisher.__new__(
        ThemeAfterCloseRecoveryPublisher
    )
    publisher.state = SimpleNamespace()
    publisher.theme_service = FakeThemeService()
    publisher.theme_members = {
        "T": (("000001", 0.6), ("000002", 0.4)),
    }
    publisher.last_error = None
    publisher.publish_count = 0
    publisher.last_publish_at = None
    publisher.last_target_date = None
    publisher.last_min_coverage = None
    return publisher


def metadata(value, source, estimated, quality, trading_date="20260710"):
    return {
        "value": value,
        "source": source,
        "status": "EXACT" if not estimated else "ESTIMATED",
        "basis_time": f"{trading_date}200000",
        "trading_date": trading_date,
        "market_scope": "AL" if source == "CLOSE_SAMPLER_EXACT" else "KRX",
        "quality": quality,
        "is_estimated": estimated,
        "coverage": 1.0,
    }


def test_theme_recovery_aggregates_without_upscaling_partial_data():
    publisher = publisher_for_test()
    rows = {
        "000001": {
            "trade_value_1m_eok": 10.0,
            "trade_value_5m_eok": 50.0,
            "source_metadata": {
                "trade_value_1m_eok": metadata(
                    10.0, "CLOSE_SAMPLER_EXACT", False, 1.0
                ),
                "trade_value_5m_eok": metadata(
                    50.0, "CLOSE_SAMPLER_EXACT", False, 1.0
                ),
            },
        },
        "000002": {
            "trade_value_1m_eok": 20.0,
            "source_metadata": {
                "trade_value_1m_eok": metadata(
                    20.0, "MINUTE_CLOSE_X_VOLUME", True, 0.65
                ),
            },
        },
    }
    themes = publisher.aggregate(rows, "20260710")
    one = themes["T"]["trade_value_1m_eok"]
    five = themes["T"]["trade_value_5m_eok"]

    assert one["value"] == 14.0
    assert one["coverage"] == 1.0
    assert one["is_estimated"] is True
    assert one["source"] == "MIXED_AFTER_CLOSE_RECOVERY"
    assert one["market_scope"] == "MIXED"

    # Only 60% of the theme is available. Do not scale 30 back up to 50.
    assert five["value"] == 30.0
    assert five["coverage"] == 0.6
    assert five["is_estimated"] is False


def test_theme_recovery_rejects_wrong_trading_date():
    publisher = publisher_for_test()
    rows = {
        "000001": {
            "trade_value_1m_eok": 10.0,
            "source_metadata": {
                "trade_value_1m_eok": metadata(
                    10.0,
                    "CLOSE_SAMPLER_EXACT",
                    False,
                    1.0,
                    trading_date="20260709",
                )
            },
        }
    }
    assert publisher.aggregate(rows, "20260710") == {}


def test_theme_recovery_publishes_estimate_and_coverage_markers():
    publisher = publisher_for_test()
    themes = {
        "T": {
            "trade_value_1m_eok": {
                "value": 14.0,
                "coverage": 1.0,
                "quality": 0.86,
                "is_estimated": True,
                "source": "MIXED_AFTER_CLOSE_RECOVERY",
                "market_scope": "MIXED",
                "basis_time": "20260710200000",
            },
            "trade_value_5m_eok": {
                "value": 30.0,
                "coverage": 0.6,
                "quality": 1.0,
                "is_estimated": False,
                "source": "CLOSE_SAMPLER_EXACT",
                "market_scope": "AL",
                "basis_time": "20260710200000",
            },
        }
    }
    assert publisher.publish(themes, "20260710") is True

    service = publisher.theme_service
    theme = service.snapshot["themes"][0]
    assert theme["inflow_1m_text"] == "≈+14억"
    assert theme["inflow_1m_recovery_label"] == "추정값"
    assert theme["inflow_5m_text"] == "+30억*"
    assert theme["inflow_5m_recovery_label"] == "Coverage 60%"
    assert service.cache_version == 5
    assert service.persist_count == 1
    assert service.snapshot["status"]["after_close_recovery_min_coverage"] == 0.6


def test_format_eok_variants():
    assert _format_eok(12, False, False) == "+12억"
    assert _format_eok(12, True, False) == "≈+12억"
    assert _format_eok(12, True, True) == "≈+12억*"


def test_board_platform_installs_theme_recovery_publisher():
    root = Path(__file__).resolve().parents[1]
    platform = (root / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "install_theme_recovery(base)" in platform
