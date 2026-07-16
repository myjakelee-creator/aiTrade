from __future__ import annotations

from realtime_v2.tr_singleflight import get_shared_tr_coordinator


def install(base) -> None:
    """Route all auxiliary metrics through one calendar-driven low-load owner.

    The production price collector remains the only QAx owner. Bid/ask, five-minute
    strength, and large trades share one REST thread and one single-flight budget.
    Execution strength uses one WebSocket connection for Top20 with one batch commit
    per second. The market-session manager owns Top1/Top20/Top100 scope, trading-date
    rollover, holidays, delayed openings, close completion, and restart recovery.
    """

    updater_class = getattr(base, "ProgramNetUpdater", None)
    if updater_class is None or getattr(updater_class, "_stockboard_tr_singleflight_installed", False):
        return

    def patched_load_existing_snapshots(self) -> None:
        # Daily-state alignment is the only trusted bootstrap. The historical docs
        # snapshot has no reliable source-trading-date and must never be re-labelled.
        with self.state.lock:
            self.state.status["program_snapshot_bootstrap"] = "calendar_daily_state_only"

    def patched_fetch_once(self) -> None:
        from realtime_v2.worker_market_metric_session_manager import (
            market_metric_phase,
            metric_target_trading_date,
        )

        coordinator = get_shared_tr_coordinator()
        trade_date = metric_target_trading_date()
        phase = market_metric_phase()
        if not trade_date:
            self.state.set_program_net_error("program target trading date unresolved")
            return

        def physical_fetch():
            from kiwoom_data_provider import fetch_program_net, issue_access_token

            token = issue_access_token()
            return fetch_program_net(token, trade_date)

        try:
            result = coordinator.execute(
                provider="kiwoom_rest",
                tr_code="ka90004_program_net",
                params={"scope": "stockboard_universe", "target_date": trade_date},
                trading_date=trade_date,
                market_session=phase,
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
                self.state.status["program_request_target_trading_date"] = trade_date
                self.state.status["program_request_market_phase"] = phase
        except Exception as error:
            self.state.set_program_net_error(str(error))
            with self.state.lock:
                self.state.status["tr_singleflight"] = coordinator.status()

    def patched_program_run(self) -> None:
        self._load_existing_snapshots()
        if not self.stop_event.is_set():
            self._fetch_once()
        while not self.stop_event.wait(self.interval_sec):
            self._fetch_once()

    updater_class._load_existing_snapshots = patched_load_existing_snapshots
    updater_class._fetch_once = patched_fetch_once
    updater_class.run = patched_program_run
    updater_class._stockboard_tr_singleflight_installed = True

    from realtime_v2.worker_metric_restore_patch import install as install_metric_restore
    from realtime_v2.worker_rest_live_metrics_patch import (
        _read_config as read_live_metric_config,
        install as install_rest_live_metrics,
    )
    from realtime_v2.worker_rest_live_metrics_aftermarket_patch import (
        install as install_rest_live_metrics_aftermarket,
    )
    from realtime_v2.worker_rest_metrics_before_market_patch import (
        install as install_before_market_backfill,
    )
    from realtime_v2.worker_rest_live_metric_metadata_patch import (
        install as install_rest_live_metric_metadata,
    )
    from realtime_v2.worker_rest_live_metrics_stage2_fix_patch import (
        install as install_rest_live_metrics_stage2_fix,
    )
    from realtime_v2.worker_large_trade_stage4_patch import (
        install as install_large_trade_stage4,
    )
    from realtime_v2.worker_strength5_only_patch import install as install_strength5_only
    from realtime_v2 import worker_realtime_strength_ws_patch as realtime_strength_module
    from realtime_v2.worker_realtime_strength_ws_patch import (
        install as install_realtime_strength_ws,
    )
    from realtime_v2.worker_realtime_strength_ws_coalesce_patch import (
        install as install_realtime_strength_ws_coalesce,
    )
    from realtime_v2.worker_realtime_strength_ws_top20_patch import (
        install as install_realtime_strength_ws_top20,
    )
    from realtime_v2.worker_metric_provenance_patch import (
        install as install_metric_provenance,
    )
    from realtime_v2.worker_market_metric_session_manager import (
        install as install_market_metric_session_manager,
        market_metric_phase,
    )
    from realtime_v2.worker_metric_state_overlay_patch import (
        install as install_metric_state_overlay,
    )
    from realtime_v2.worker_six_metric_lifecycle_runtime_opt import (
        install as install_six_metric_runtime_opt,
    )
    from realtime_v2.worker_six_metric_lifecycle_patch import (
        install as install_six_metric_lifecycle,
    )
    from realtime_v2.worker_six_metric_output_guard import (
        install as install_six_metric_output_guard,
    )
    from realtime_v2.worker_orderbook_live_display_guard import (
        install as install_orderbook_live_display_guard,
    )
    from realtime_v2.html_null_metric_patch import install as install_html_null_metric
    from realtime_v2.html_execution_strength_label_patch import (
        install as install_execution_strength_label,
    )
    from realtime_v2.html_opening_render_guard_patch import (
        install as install_opening_render_guard,
    )

    install_metric_restore(base)
    install_rest_live_metrics(base)
    install_rest_live_metrics_aftermarket()
    install_before_market_backfill()
    install_rest_live_metric_metadata(base)
    install_rest_live_metrics_stage2_fix(base)
    install_large_trade_stage4(base)
    install_strength5_only()
    install_realtime_strength_ws(base)
    install_realtime_strength_ws_coalesce(base)
    realtime_strength_module._read_config = read_live_metric_config

    def execution_ws_phase(config):
        phase = market_metric_phase(config=config)
        if phase in {"opening_burst", "regular", "closing_call", "aftermarket"}:
            return phase
        return "outside"

    realtime_strength_module._session_phase = execution_ws_phase
    install_realtime_strength_ws_top20(base)
    install_metric_provenance(base)
    install_market_metric_session_manager(base)
    install_metric_state_overlay(base)
    install_six_metric_runtime_opt()
    install_six_metric_lifecycle(base)
    install_six_metric_output_guard(base)
    install_orderbook_live_display_guard(base)
    install_html_null_metric()
    install_execution_strength_label()
    install_opening_render_guard()
