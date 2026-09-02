"""Assemble the full ground-truth fixture: epistemic layers, rubrics, and assertions."""

from typing import Any

from dataset.core import state
from dataset.core.constants import DEFAULT_LOCALE, GENERATED_AT, POLICY_VERSION
from dataset.ground_truth.assertions import build_relationship_assertions
from dataset.ground_truth.crosswalks import build_event_crosswalks
from dataset.ground_truth.entities import build_entities
from dataset.ground_truth.mentions import build_mentions_and_resolutions
from dataset.ground_truth.resolutions import build_resolution_decisions
from dataset.ground_truth.rubrics import build_golden_questions
from dataset.provenance import build_provenance_dags


def build_ground_truth(
    ref_catalog: dict[str, dict[str, Any]],
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
) -> dict[str, Any]:
    entities = build_entities()

    entity_mentions, mention_resolutions, actor_resolution_candidates, mention_source_refs = (
        build_mentions_and_resolutions(ref_catalog, cdr, extraction)
    )
    resolution_decisions = build_resolution_decisions(mention_resolutions)
    assertions = build_relationship_assertions(ref_catalog)
    event_crosswalks = build_event_crosswalks(ref_catalog)

    provenance_dags, provenance_source_refs = build_provenance_dags(
        ref_catalog,
        assertions,
        event_crosswalks,
        entity_mentions,
        mention_resolutions,
        resolution_decisions,
        mention_source_refs,
    )
    structuring_dag_id = provenance_dags[0]["dag_id"]
    comms_dag_id = provenance_dags[1]["dag_id"]

    golden_questions, rubric_source_refs = build_golden_questions(
        ref_catalog, structuring_dag_id, comms_dag_id
    )

    forbidden_claims = [
        "P-A and P-D are the same person.",
        "P-A and P-S are the same person.",
        "The usual user of a phone necessarily authored every message.",
        "U-1 has a known person identity.",
        "A named person committed smuggling or laundering.",
        "The source of Aegean's funds is established.",
        "P-A controls Meridian as a registry fact.",
        "R-03 is an exact transaction match.",
        "Co-location, kinship, co-occurrence, amount similarity or a shared landlord proves culpability.",
        "A top-k retrieval miss proves non-existence.",
        "A named person intentionally planted A-D1.",
    ]

    source_refs_by_id = {
        ref["source_ref_id"]: ref for ref in provenance_source_refs + rubric_source_refs
    }
    for assertion in assertions:
        for ref in assertion["supporting_refs"]:
            source_refs_by_id[ref["source_ref_id"]] = ref

    return {
        "metadata": {
            "dataset_version": state.DATASET_VERSION,
            "language": state.ACTIVE_LOCALE,
            "edition_role": "primary" if state.ACTIVE_LOCALE == DEFAULT_LOCALE else "alternate",
            "policy_version": POLICY_VERSION,
            "generated_at": GENERATED_AT,
            "test_only": True,
            "runtime_indexing_allowed": False,
        },
        "epistemic_layers": {
            "latent_scenario_truth": {
                "test_only": True,
                "authored_scenario": "false-service-invoice laundering pattern; smuggling remains only a possible narrative",
                "absent_from_observable_evidence": [
                    "predicate offense",
                    "historic source of Aegean's opening balance",
                ],
                "person_distinctions": [
                    "P-A and P-D are distinct reported cousins.",
                    "P-S is distinct and used P-A's phone only during the pinned Feb-21 interval.",
                    "U-1's person identity is unavailable.",
                ],
                "hard_negative_records": [
                    "c14",
                    "c15",
                    "c16",
                    "c17",
                    "c27",
                    "nT04",
                    "nT05",
                    "N-D1",
                    "N-D2",
                    "N-D3",
                    "A-D1",
                ],
            },
            "observable_entities_resolutions_assertions": {
                "canonical_entities": entities,
                "source_refs": sorted(
                    source_refs_by_id.values(), key=lambda ref: ref["source_ref_id"]
                ),
                "entity_mentions": entity_mentions,
                "mention_resolutions": mention_resolutions,
                "resolution_decisions": resolution_decisions,
                "actor_resolution_candidates": actor_resolution_candidates,
                "forbidden_person_merges": [
                    ["ent_person_mavridis_a", "ent_person_mavridis_d"],
                    ["ent_person_mavridis_a", "ent_person_sofia"],
                ],
                "relationship_assertions": assertions,
                "event_crosswalks": event_crosswalks,
                "provenance_dags": provenance_dags,
                "background_reconciliation_control": {
                    "extraction_record": "X-N01",
                    "candidate_cdr_records": ["c15", "c16"],
                    "expected_action": "abstain",
                    "reason": "multiple policy-compatible candidates inside 90 seconds",
                },
            },
            "allowed_inferences": [
                "The phones attributed in cited records communicated.",
                "Aegean sent the booked transfers t_85, t_86 and t_88.",
                "The records support a reviewable association between P-A and Meridian.",
                "Timing can be consistent with coordination without establishing causation.",
                "R-03 approximately refers to t_88 under the pinned policy.",
                "No configured flag fired for Elena inside the complete structured scope.",
                "The in-window extract contains no credit to acct_aegean; opening-balance history is unknown.",
            ],
            "forbidden_claims": forbidden_claims,
        },
        "approximate_reference_expected": {
            "source_record": "R-03",
            "candidate_transaction": "t_88",
            "status": "candidate_only",
            "excluded_transaction": "t_86",
            "exclusion_reason": "named counterparty does not match Meridian",
            "without_counterparty_constraint": "ambiguous_abstain",
            "policy_version": POLICY_VERSION,
        },
        "screening_outcomes": {
            "must_fire": [
                {
                    "rule": "structuring_sub_threshold",
                    "subject_entity_id": "ent_org_aegean",
                    "triggering_records": ["t_85", "t_86", "t_88"],
                    "provenance_dag_id": structuring_dag_id,
                    "policy_version": POLICY_VERSION,
                },
                {
                    "rule": "comms_before_transfer",
                    "actor_pair": ["ent_person_mavridis_a", "ent_person_rossi"],
                    "transfer_record": "t_88",
                    "canonical_events": [
                        {"event_id": "email:eM1", "source_records": ["eM1"]},
                        {
                            "event_id": "same-event:c01:X-204:reconciliation@1",
                            "source_records": ["c01", "X-204"],
                        },
                        {
                            "event_id": "same-event:c02:X-205:reconciliation@1",
                            "source_records": ["c02", "X-205"],
                        },
                        {"event_id": "call:c06", "source_records": ["c06"]},
                    ],
                    "identity_dependency_records": ["R-01", "R-02", "eM2"],
                    "excluded_24h_record": "c14",
                    "provenance_dag_id": comms_dag_id,
                    "authorship_semantics": "attributed_endpoints_not_proven_person_authorship",
                    "policy_version": POLICY_VERSION,
                },
            ],
            "must_not_fire": [
                {
                    "rule": "structuring_sub_threshold",
                    "subject_entity_id": "ent_person_mavridis_d",
                    "near_miss_records": ["t_B1"],
                },
                {
                    "rule": "any_configured_rule",
                    "subject_entity_id": "ent_person_vasileiou_n1",
                    "profile_records": ["nA01", "nT01", "nT02", "nT03", "eM4"],
                },
                {
                    "rule": "story_flag_from_shared_feature_only",
                    "hard_negative_records": ["c17", "c27", "nT04", "nT05", "N-D2", "N-D3"],
                },
            ],
        },
        "golden_questions": golden_questions,
    }
