from __future__ import annotations

PATCH_VERSION = "ka10046_strength5_only_v1"
ALLOWED_KEYS = {
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "strength_source",
    "strength_status",
    "strength_snapshot_at",
    "last_valid_strength_5m",
    "last_valid_strength_at",
}


def install() -> None:
    """Prevent ka10046 trend polling from overwriting realtime FID228 strength."""

    import realtime_v2.worker_rest_live_metrics_patch as module

    updater_class = module.RestLiveMetricUpdater
    if getattr(updater_class, "_stockboard_strength5_only_installed", False):
        return
    original_apply = updater_class._apply

    def apply(self, code: str, metric: str, payload: dict) -> bool:
        if metric != "strength":
            return original_apply(self, code, metric, payload)
        values = module.parse_strength_payload(
            payload,
            apply_five_minute=True,
            updated_at=module.now_text(),
        )
        filtered = {
            key: value
            for key, value in values.items()
            if key in ALLOWED_KEYS and value not in (None, "")
        }
        if not filtered.get("strength_5m"):
            return False
        self.state.apply_rest_live_metric_values(code, filtered, metric)
        with self.state.lock:
            self.state.status["strength5_only_patch"] = PATCH_VERSION
            self.state.status["strength5_only_last_code"] = code
            self.state.status["strength5_only_last_at"] = module.now_text()
        return True

    updater_class._apply = apply
    updater_class._stockboard_strength5_only_installed = True
