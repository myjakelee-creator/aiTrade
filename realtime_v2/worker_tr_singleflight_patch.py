from __future__ import annotations

from realtime_v2.common import trading_date_text
from realtime_v2.tr_singleflight import get_shared_tr_coordinator


def install(base) -> None:
    """Route slow REST sources through shared low-priority worker lanes.

    The large-trade QAx sidecar remains production-disabled. Restored bid/ask,
    strength, and large-trade metrics use official Kiwoom REST endpoints from one
    sleeping worker thread and never alter the verified price collector.
    """

    updater_class = getattr(base, "ProgramNetUpdater", None)
    if updater_class is None or getattr(updater_class, "_stockboard_tr_singleflight_installed", False):
        return

    def patched_fetch_once(self) -> None:
        coordinator = get_shared_tr_coordinator()
        trade_date = trading_date_text()

        def physical_fetch():
            from kiwoom_data_provider import fetch_program_net, issue_access_token

            token = issue_access_token()
            return fetch_program_net(token, trade_date)

        try:
            result = coordinator.execute(
                provider="kiwoom_rest",
                tr_code="ka90004_program_net",
                params={"scope": "stockboard_universe"},
                trading_date=trade_date,
                market_session="regular_or_latest",
                ttl_sec=max(15.0, float(self.interval_sec) * 0.8),
                wait_timeout_sec=max(30.0, float(self.interval_sec)),
                fetcher=physical_fetch,
            )
            values = result.get("values") if isinstance(result, dict) else None
            if isinstance(values, dict):
                status = "partial" if result.get("errors") or result.get("rate_limit") else "ok"
                self.state.apply_program_net_values(
                    values,
                    "ka90004_tr_singleflight",
                    status,
                )
            with self.state.lock:
                self.state.status["tr_singleflight"] = coordinator.status()
        except Exception as error:
            self.state.set_program_net_error(str(error))
            with self.state.lock:
                self.state.status["tr_singleflight"] = coordinator.status()

    updater_class._fetch_once = patched_fetch_once
    updater_class._stockboard_tr_singleflight_installed = True

    from realtime_v2.worker_metric_restore_patch import install as install_metric_restore
    from realtime_v2.worker_rest_live_metrics_patch import install as install_rest_live_metrics

    install_metric_restore(base)
    install_rest_live_metrics(base)
