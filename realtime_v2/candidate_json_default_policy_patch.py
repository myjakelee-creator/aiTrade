from __future__ import annotations


def install() -> None:
    import stockboard_candidate_config as config
    import stockboard_candidate_features as features

    snapshot_class = features.FeatureSnapshot
    if getattr(snapshot_class, "_stockboard_default_json_policy_installed", False):
        return

    original_init = snapshot_class.__init__

    def patched_init(self, rows, feature_policies=None):
        policies = feature_policies
        if policies is None:
            policies = config.load_candidate_model_config().get("feature_policies")
        original_init(self, rows, policies)

    snapshot_class.__init__ = patched_init
    snapshot_class._stockboard_default_json_policy_installed = True
