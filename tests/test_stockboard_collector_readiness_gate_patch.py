from types import SimpleNamespace

from realtime_v2.collector_readiness_gate_patch import install


class Provider:
    def __init__(self, *, login_state="requested", realreg_succeeded=False, count=198):
        self.payload = {
            "login_state": login_state,
            "realreg_succeeded": realreg_succeeded,
            "realreg_code_count": count,
        }

    def status(self):
        return dict(self.payload)


def make_base():
    published = []

    def publish_collector_status(_sender, _provider, extra=None):
        published.append(dict(extra or {}))

    return SimpleNamespace(
        publish_collector_status=publish_collector_status,
        published=published,
    )


def test_requested_login_does_not_report_registered_count():
    base = make_base()
    install(base)

    base.publish_collector_status(None, Provider(), {"registered_count": 198})

    payload = base.published[-1]
    assert payload["registered_count_requested"] == 198
    assert payload["registered_count"] == 0
    assert payload["collector_ready"] is False
    assert payload["collector_ready_reason"] == "waiting_login"


def test_connected_login_waits_for_realreg_success():
    base = make_base()
    install(base)

    provider = Provider(login_state="connected", realreg_succeeded=False, count=198)
    base.publish_collector_status(None, provider, {"registered_count": 198})

    payload = base.published[-1]
    assert payload["registered_count"] == 0
    assert payload["collector_ready"] is False
    assert payload["collector_ready_reason"] == "waiting_realreg"


def test_ready_provider_reports_actual_registered_count():
    base = make_base()
    install(base)

    provider = Provider(login_state="connected", realreg_succeeded=True, count=198)
    base.publish_collector_status(None, provider, {"registered_count": 198})

    payload = base.published[-1]
    assert payload["registered_count"] == 198
    assert payload["collector_ready"] is True
    assert payload["collector_ready_reason"] == "login_connected_and_realreg_succeeded"


def test_install_is_idempotent():
    base = make_base()
    install(base)
    first = base.publish_collector_status
    install(base)
    assert base.publish_collector_status is first
