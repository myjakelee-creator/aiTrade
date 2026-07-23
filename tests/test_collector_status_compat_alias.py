from __future__ import annotations

from types import SimpleNamespace

from realtime_v2 import collector_sender_resilience_patch as patch


class _Sender:
    def __init__(self, *_args, **_kwargs):
        self.stop_event = SimpleNamespace(is_set=lambda: True)
        self.sent_count = 0
        self.last_flush_at = None
        self.connected = True

    def stats(self):
        return {"connected": True, "sent_count": 3}

    def is_alive(self):
        return True


class _Provider:
    def status(self):
        return {
            "login_state": "connected",
            "realreg_succeeded": True,
            "realreg_code_count": 100,
            "openapi_native_handle_ready": True,
            "openapi_native_hwnd": 12345,
        }


def test_collector_status_exposes_legacy_and_current_shapes():
    captured = []

    def original_publish(sender, provider, extra=None):
        payload = {
            "provider": provider.status(),
            "sender": sender.stats(),
        }
        payload.update(extra or {})
        captured.append(payload)

    base = SimpleNamespace(
        EventSender=_Sender,
        publish_collector_status=original_publish,
        safe_json_dumps=lambda value: str(value),
        now_text=lambda: "2026-07-23T19:40:00+09:00",
    )

    patch.install(base)
    sender = base.EventSender()
    provider = _Provider()
    base.publish_collector_status(
        sender,
        provider,
        {
            "provider_started": True,
            "registered_count": 100,
            "collector_ready": True,
        },
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["provider"] == payload["status"]
    assert payload["sender"] == payload["sender_stats"]
    assert payload["status"]["login_state"] == "connected"
    assert payload["status"]["realreg_succeeded"] is True
    assert payload["status"]["realreg_code_count"] == 100
    assert payload["provider_started"] is True
    assert payload["registered_count"] == 100
    assert payload["collector_ready"] is True
    assert payload["collector_status_compat_version"] == patch.STATUS_COMPAT_VERSION
