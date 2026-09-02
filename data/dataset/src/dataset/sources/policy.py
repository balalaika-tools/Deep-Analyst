"""Build the versioned screening/reconciliation/identity-resolution policy."""

from typing import Any

from dataset.core.constants import POLICY_VERSION


def build_policy() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "reconciliation": {
            "timestamp_tolerance_seconds": 90,
            "normalize_device_direction_to_network_roles": True,
            "require_compatible_imei_or_subscriber": True,
            "require_same_sender_recipient": True,
            "ambiguous_candidate_action": "abstain",
        },
        "identity_resolution": {
            "exact_same_type_asset": "auto",
            "shared_phone_person_merge": "never",
            "shared_iban_person_or_org_merge": "never",
            "cross_script_name_only": "review",
            "scorer_version": "actor-resolution-score@1",
            "transliteration_rules_version": "el-latin@1",
            "score_semantics": "ranking_feature_not_probability",
            "feature_aggregation": "weighted_sum_clamped_0_1",
            "feature_weights": {
                "normalized_full_name_similarity": 0.45,
                "explicit_alias_assertion": 0.30,
                "independent_source_corroboration": 0.15,
                "compatible_temporal_context": 0.10,
            },
            "authoritative_actor_key_rules": [],
            "theta_auto": 0.90,
            "theta_min": 0.65,
            "tier2_min_independent_corroborators": 2,
            "conflict_action": "review",
        },
        "approximate_reference_matching": {
            "amount_tolerance_minor": 50000,
            "date_tolerance_days": 1,
            "require_same_currency": True,
            "require_named_counterparty_when_present": True,
            "result_status": "candidate_only",
        },
        "screening": {
            "structuring_sub_threshold": {
                "currency": "EUR",
                "threshold_minor": 1000000,
                "lower_bound_minor": 900000,
                "minimum_count": 3,
                "window_business_days": 3,
                "require_activity_each_business_day": True,
                "group_by": "debtor_entity",
                "statuses": ["booked"],
            },
            "comms_before_transfer": {
                "lookback_hours": 24,
                "minimum_events": 2,
                "minimum_transfer_minor": 900000,
                "group_by": "resolved_actor_pair",
                "directions": "either",
                "allowed_identity_statuses": ["confirmed"],
                "count_distinct_canonical_events": True,
                "paired_record_collapse": "SAME_EVENT_AS",
                "authorship_semantics": "attributed_endpoints_not_proven_person_authorship",
            },
        },
    }
