import json
from pathlib import Path

import pytest

from realtime_v2.market_supply_last_valid_patch import (
    _read_json_with_encoding,
    market_supply_valid,
    normalize_market_supply,
)


def _valid_entry(index: float) -> dict:
    return {
        "market_index": index,
        "market_change_rate": 1.23,
        "advancers": 100,
        "decliners": 50,
    }


def _valid_market_supply_payload() -> dict:
    return {
        "kospi": _valid_entry(2800.0),
        "kosdaq": _valid_entry(900.0),
        "_status": {"available": True, "status": "available", "errors": []},
    }


def _auth_failed_market_supply_payload() -> dict:
    message = "ka20001 KOSPI failed: 인증에 실패했습니다[8005]"
    return {
        "kospi": {
            "market_name": "KOSPI",
            "market_index": None,
            "market_change_rate": None,
            "advancers": None,
            "decliners": None,
            "error": message,
        },
        "kosdaq": {
            "market_name": "KOSDAQ",
            "market_index": None,
            "market_change_rate": None,
            "advancers": None,
            "decliners": None,
            "error": message,
        },
        "_status": {
            "available": False,
            "status": "unavailable",
            "errors": [{"api": "ka20001", "error": message}],
        },
    }


def test_market_supply_valid_requires_both_markets():
    assert market_supply_valid(
        {
            "kospi": _valid_entry(2800.0),
            "kosdaq": _valid_entry(900.0),
        }
    )
    assert not market_supply_valid({"kospi": _valid_entry(2800.0)})


def test_market_supply_valid_rejects_blank_weekend_payload():
    assert not market_supply_valid(
        {
            "kospi": {
                "market_index": None,
                "market_change_rate": None,
                "advancers": 0,
                "decliners": 0,
            },
            "kosdaq": {
                "market_index": None,
                "market_change_rate": None,
                "advancers": 0,
                "decliners": 0,
            },
        }
    )


def test_normalize_supports_uppercase_and_wrapped_values():
    normalized = normalize_market_supply(
        {
            "values": {
                "KOSPI": {
                    "index": "2800.1",
                    "change_rate": "1.2",
                    "advance": "100",
                    "decline": "50",
                },
                "KOSDAQ": {
                    "index": "900.2",
                    "change_rate": "-0.3",
                    "advance": "80",
                    "decline": "70",
                },
            }
        }
    )
    assert normalized["kospi"]["market_index"] == "2800.1"
    assert normalized["kosdaq"]["decliners"] == "70"
    assert market_supply_valid(normalized)


def test_normalize_recursively_finds_deep_market_rows():
    normalized = normalize_market_supply(
        {
            "outer": {
                "payload": {
                    "items": [
                        {
                            "market_name": "KOSPI",
                            "cur_prc": "2801.5",
                            "flu_rt": "1.1",
                            "rising": "510",
                            "fall": "320",
                        },
                        {
                            "market_name": "KOSDAQ",
                            "cur_prc": "901.4",
                            "flu_rt": "-0.2",
                            "rising": "620",
                            "fall": "710",
                        },
                    ]
                }
            }
        }
    )
    assert normalized["kospi"]["market_index"] == "2801.5"
    assert normalized["kosdaq"]["advancers"] == "620"
    assert market_supply_valid(normalized)


def test_read_json_supports_windows_powershell_utf16(tmp_path):
    path = tmp_path / "market_supply_after.json"
    payload = {
        "KOSPI": {
            "index": 2800.0,
            "change_rate": 1.0,
            "advance": 100,
            "decline": 50,
        },
        "KOSDAQ": {
            "index": 900.0,
            "change_rate": -0.5,
            "advance": 80,
            "decline": 70,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-16")

    loaded, encoding = _read_json_with_encoding(path)

    assert encoding == "utf-16"
    assert loaded == payload
    assert market_supply_valid(loaded)


def _reset_context_writer_token_state(writer) -> None:
    writer._MARKET_SUPPLY_ACCESS_TOKEN = None
    writer._MARKET_SUPPLY_TOKEN_ISSUED_AT = None
    writer._MARKET_SUPPLY_TOKEN_ISSUED_MONOTONIC = None
    writer._MARKET_SUPPLY_TOKEN_ISSUE_COUNT = 0
    writer._MARKET_SUPPLY_TOKEN_REFRESH_COUNT = 0
    writer._MARKET_SUPPLY_TOKEN_LAST_REASON = None


def test_live_market_supply_refreshes_expired_token_once(monkeypatch):
    import kiwoom_data_provider
    from realtime_v2 import context_snapshot_writer as writer

    _reset_context_writer_token_state(writer)
    writer._MARKET_SUPPLY_ACCESS_TOKEN = "expired-token"
    issued: list[str] = []
    used_tokens: list[str] = []

    def fake_issue_access_token():
        issued.append("fresh-token")
        return "fresh-token"

    def fake_fetch_market_supply(token, _query_date):
        used_tokens.append(token)
        if token == "expired-token":
            return _auth_failed_market_supply_payload()
        return _valid_market_supply_payload()

    monkeypatch.setattr(
        kiwoom_data_provider,
        "issue_access_token",
        fake_issue_access_token,
    )
    monkeypatch.setattr(
        kiwoom_data_provider,
        "fetch_market_supply",
        fake_fetch_market_supply,
    )

    result = writer.fetch_live_market_supply_snapshot()

    assert used_tokens == ["expired-token", "fresh-token"]
    assert issued == ["fresh-token"]
    assert result["token_retry_used"] is True
    assert result["market_supply_token_refresh_count"] == 1
    assert result["market_supply_token_last_reason"] == "auth_failure_response"
    assert market_supply_valid(result)


def test_live_market_supply_does_not_refresh_non_auth_empty_payload(monkeypatch):
    import kiwoom_data_provider
    from realtime_v2 import context_snapshot_writer as writer

    _reset_context_writer_token_state(writer)
    writer._MARKET_SUPPLY_ACCESS_TOKEN = "valid-token"
    issue_count = 0

    def fake_issue_access_token():
        nonlocal issue_count
        issue_count += 1
        return "unexpected-token"

    def fake_fetch_market_supply(_token, _query_date):
        payload = _auth_failed_market_supply_payload()
        payload["_status"]["errors"][0]["error"] = "no aggregate market flow row"
        payload["kospi"]["error"] = "no aggregate market flow row"
        payload["kosdaq"]["error"] = "no aggregate market flow row"
        return payload

    monkeypatch.setattr(
        kiwoom_data_provider,
        "issue_access_token",
        fake_issue_access_token,
    )
    monkeypatch.setattr(
        kiwoom_data_provider,
        "fetch_market_supply",
        fake_fetch_market_supply,
    )

    with pytest.raises(RuntimeError, match="preserving last valid snapshot"):
        writer.fetch_live_market_supply_snapshot()

    assert issue_count == 0
    assert writer._MARKET_SUPPLY_TOKEN_REFRESH_COUNT == 0


def test_worker_installs_market_supply_hold_on_actual_context_module():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "worker64_guarded_large_bidask.py"
    ).read_text(encoding="utf-8")
    assert "_install_market_supply_hold_fail_open()" in source
    assert 'context_module = getattr(large, "guarded", large)' in source
    assert "market_supply_last_valid_patch_error.txt" in source


def test_context_patch_is_fail_open():
    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "market_supply_last_valid_patch.py"
    ).read_text(encoding="utf-8")
    assert '"market_supply_display_basis": "ORIGINAL_CONTEXT_ERROR"' in source
    assert 'payload["market_supply_display_basis"] = "PATCH_FAIL_OPEN"' in source
    assert "market_supply_patch_traceback" in source
