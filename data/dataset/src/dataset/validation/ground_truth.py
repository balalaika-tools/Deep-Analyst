"""Validate the ground-truth epistemic layer: golden questions, assertions,
mentions/resolutions, source refs, provenance DAGs, and screening outcomes."""

from typing import Any

from dataset.core.constants import POLICY_VERSION
from dataset.core.util import _json_bytes, _require, _sha256
from dataset.provenance import _nested_value


def _validate_source_ref(ref: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> None:
    record_id = ref["source_record_id"]
    _require(record_id in catalog, "SourceRef points to a missing record")
    meta = catalog[record_id]
    _require(ref["record_id"] == meta["record_id"], "SourceRef record mismatch")
    _require(ref["source_system"] == meta["source"], "SourceRef source mismatch")
    _require(ref["source_version_id"] == meta["source_version"], "SourceRef version mismatch")
    _require(
        ref["record_version_id"] == meta["record_version_id"], "SourceRef record version mismatch"
    )
    _require(ref["raw_content_hash"] == meta["raw_content_hash"], "SourceRef content hash mismatch")
    _require(ref["raw_object_uri"] == meta["raw_path"], "SourceRef path mismatch")
    payload = dict(ref)
    source_ref_id = payload.pop("source_ref_id")
    expected_id = "src:{}:{}".format(meta["record_version_id"], _sha256(_json_bytes(payload)))
    _require(source_ref_id == expected_id, "SourceRef ID hash mismatch")

    logical_record = meta["_logical_record"]
    locator = ref["locator"]
    if locator["kind"] == "field":
        _require(
            _nested_value(logical_record, locator["field_path"]) == locator["raw_value"],
            "SourceRef field value mismatch",
        )
    elif locator["kind"] == "field_set":
        for field in locator["fields"]:
            _require(
                _nested_value(logical_record, field["field_path"]) == field["raw_value"],
                "SourceRef field-set value mismatch",
            )
    elif locator["kind"] == "text_span":
        text = str(_nested_value(logical_record, locator["field_path"]))
        _require(
            text[locator["char_start"] : locator["char_end"]] == locator["raw_value"],
            "SourceRef text span mismatch",
        )
    else:
        _require(locator["kind"] == "record", "unsupported SourceRef locator")


def _validate_provenance_dag(dag: dict[str, Any], source_ref_ids: set[str]) -> None:
    nodes = {node["node_id"]: node for node in dag["nodes"]}
    _require(len(nodes) == len(dag["nodes"]), "DAG node IDs must be unique")
    _require(dag["root_node_id"] in nodes, "DAG root node missing")
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in dag["edges"]:
        _require(edge["from_node_id"] in nodes, "DAG edge source missing")
        _require(edge["to_node_id"] in nodes, "DAG edge target missing")
        adjacency[edge["from_node_id"]].append(edge["to_node_id"])

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        _require(node_id not in visiting, "provenance DAG contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    visit(dag["root_node_id"])
    _require(visited == set(nodes), "every provenance node must be reachable from root")
    for node_id, targets in adjacency.items():
        if not targets:
            node = nodes[node_id]
            _require(
                node["node_type"] in {"source_ref", "policy"},
                "DAG leaves must be immutable evidence or policy",
            )
            if node["node_type"] == "source_ref":
                _require(
                    node["source_ref_id"] in source_ref_ids, "DAG SourceRef missing from catalog"
                )


def validate_golden_questions(ground_truth: dict[str, Any]) -> None:
    questions = ground_truth["golden_questions"]
    _require(len(questions) == 12, "exactly twelve golden rubrics are required")
    required_rubric_fields = {
        "id",
        "question",
        "required_claims",
        "acceptable_evidence_sets",
        "forbidden_claims",
        "required_caveats",
        "coverage_requirements",
    }
    _require(
        all(required_rubric_fields <= set(question) for question in questions),
        "golden rubric field missing",
    )
    _require(
        len({question["id"] for question in questions}) == 12, "golden rubric IDs must be unique"
    )


def validate_observable_layer(
    ref_catalog: dict[str, dict[str, Any]],
    ground_truth: dict[str, Any],
    source_ids: set[str],
) -> None:
    observable = ground_truth["epistemic_layers"]["observable_entities_resolutions_assertions"]
    required_assertion_triples = {
        ("ent_person_mavridis_a", "USES", "ent_phone_pa"),
        ("ent_person_sofia", "USES", "ent_phone_pa"),
        ("ent_person_rossi", "USES", "ent_phone_pr"),
        ("ent_person_rossi", "DIRECTOR_OF", "ent_org_meridian"),
        ("ent_person_rossi", "ASSOCIATED_WITH", "ent_org_aegean"),
        ("ent_person_mavridis_d", "USES", "ent_phone_pd"),
        ("ent_person_mavridis_d", "KIN_OF", "ent_person_mavridis_a"),
        ("ent_person_mavridis_d", "EMPLOYED_BY", "ent_org_logistiki_b1"),
        ("ent_person_papadakis_n2", "USES", "ent_phone_n2"),
        ("ent_org_aegean", "ASSOCIATED_WITH", "ent_phone_aegean"),
        ("ent_org_logistiki_b1", "ASSOCIATED_WITH", "ent_phone_b1"),
        ("ent_account_meridian", "HELD_BY", "ent_org_meridian"),
        ("ent_person_mavridis_a", "ASSOCIATED_WITH", "ent_org_meridian"),
        ("ent_org_aegean", "PAID", "ent_org_ionian"),
        ("ent_org_aegean", "PAID", "ent_org_meridian"),
    }
    actual_assertion_triples = {
        (item["subject_entity_id"], item["predicate"], item["object_entity_id"])
        for item in observable["relationship_assertions"]
    }
    _require(required_assertion_triples <= actual_assertion_triples, "required assertion missing")
    _require(len(observable["event_crosswalks"]) == 6, "six event crosswalks expected")
    _require(
        ["ent_person_sofia", "ent_person_mavridis_a"] not in observable["forbidden_person_merges"],
        "forbidden merge tuple orientation changed unexpectedly",
    )
    _require(
        ["ent_person_mavridis_a", "ent_person_mavridis_d"] in observable["forbidden_person_merges"],
        "P-A/P-D forbidden merge missing",
    )
    _require(
        ["ent_person_mavridis_a", "ent_person_sofia"] in observable["forbidden_person_merges"],
        "P-A/P-S forbidden merge missing",
    )

    source_refs = {ref["source_ref_id"]: ref for ref in observable["source_refs"]}
    _require(len(source_refs) == len(observable["source_refs"]), "SourceRef IDs must be unique")
    for ref in source_refs.values():
        _validate_source_ref(ref, ref_catalog)

    mentions = {mention["mention_id"]: mention for mention in observable["entity_mentions"]}
    _require(len(mentions) == len(observable["entity_mentions"]), "mention IDs must be unique")
    entities = {entity["entity_id"] for entity in observable["canonical_entities"]}
    mention_resolutions = observable["mention_resolutions"]
    _require(len(mention_resolutions) == len(mentions), "every mention must have one resolution")
    _require(
        len({resolution["mention_id"] for resolution in mention_resolutions})
        == len(mention_resolutions),
        "mention resolution must be unique",
    )
    for mention in mentions.values():
        _require(mention["source_ref_id"] in source_refs, "mention SourceRef missing")
        _require(
            mention["extraction_quality"] in {"rule_validated", "span_verified", "needs_review"},
            "invalid mention extraction quality",
        )
    for resolution in mention_resolutions:
        _require(resolution["mention_id"] in mentions, "resolution points to missing mention")
        _require(resolution["entity_id"] in entities, "resolution points to missing entity")
        _require(resolution["policy_version"] == POLICY_VERSION, "resolution policy mismatch")

    counts_by_entity: dict[str, int] = {}
    for resolution in mention_resolutions:
        mention = mentions[resolution["mention_id"]]
        if mention["entity_type"] in {"PHONE", "DEVICE"}:
            counts_by_entity[resolution["entity_id"]] = (
                counts_by_entity.get(resolution["entity_id"], 0) + 1
            )
    expected_asset_counts = {
        "ent_phone_pa": 32,
        "ent_phone_pr": 22,
        "ent_phone_pd": 5,
        "ent_phone_n2": 19,
        "ent_phone_aegean": 2,
        "ent_phone_b1": 2,
        "ent_phone_u1": 1,
        "ent_device_pa": 18,
        "ent_device_pr": 2,
    }
    _require(
        counts_by_entity == expected_asset_counts, "required exact-asset mention inventory differs"
    )

    for decision in observable["resolution_decisions"]:
        _require(
            set(decision["supporting_records"]) <= source_ids,
            "resolution decision references missing record",
        )
        _require(
            set(decision["supporting_mention_ids"]) <= set(mentions),
            "resolution decision mentions missing",
        )
        _require(
            decision["policy_version"] == POLICY_VERSION, "resolution decision policy mismatch"
        )
    n8_candidate = next(
        candidate
        for candidate in observable["actor_resolution_candidates"]
        if candidate["left_entity_id"] == "ent_person_mavridou_n8"
    )
    _require(
        n8_candidate["resolution_status"] == "proposed" and n8_candidate["merge_allowed"] is False,
        "N-8 candidate semantics differ",
    )

    for assertion in observable["relationship_assertions"]:
        refs = {ref["source_record_id"] for ref in assertion["supporting_refs"]}
        _require(refs <= source_ids, "assertion references missing record")
        ref_ids = {ref["source_ref_id"] for ref in assertion["supporting_refs"]}
        _require(
            set(assertion["extraction_quality_by_ref"]) == ref_ids, "assertion quality refs differ"
        )
        _require(
            set(assertion["source_reliability_by_ref"]) == ref_ids,
            "assertion reliability refs differ",
        )
        _require(
            set(assertion["extraction_quality_by_ref"].values())
            <= {"rule_validated", "span_verified", "needs_review"},
            "invalid assertion extraction quality",
        )
        _require(
            set(assertion["source_reliability_by_ref"].values())
            <= {"high", "medium", "low", "unknown"},
            "invalid assertion reliability",
        )
        _require(
            assertion["assertion_status"]
            in {"confirmed", "proposed", "rejected", "disputed", "not_applicable"},
            "invalid assertion status",
        )
        _require(
            assertion["identity_status"]
            in {"confirmed", "proposed", "rejected", "disputed", "not_applicable"},
            "invalid assertion identity status",
        )
        payload = dict(assertion)
        assertion_id = payload.pop("assertion_id")
        digest = _sha256(_json_bytes(payload))
        expected_assertion_id = "rel:{}:{}:{}:{}".format(
            assertion["subject_entity_id"],
            assertion["predicate"],
            assertion["object_entity_id"],
            digest,
        )
        _require(assertion_id == expected_assertion_id, "assertion ID hash mismatch")

    for question in ground_truth["golden_questions"]:
        for evidence_set in question["acceptable_evidence_sets"]:
            _require(set(evidence_set) <= source_ids, "golden rubric references missing record")
        for claim in question["claim_provenance"]:
            _require(
                set(claim["supporting_source_ref_ids"]) <= set(source_refs),
                "claim provenance SourceRef missing",
            )

    dags = observable["provenance_dags"]
    _require(len(dags) == 2, "two screening provenance DAGs expected")
    for dag in dags:
        _validate_provenance_dag(dag, set(source_refs))
    dag_ids = {dag["dag_id"] for dag in dags}

    structuring_dag = next(
        dag
        for dag in dags
        if dag["nodes"][0].get("object_key") == "structuring_sub_threshold"
        or any(node.get("object_key") == "structuring_sub_threshold" for node in dag["nodes"])
    )
    holder_resolution_node = "mention-resolution:{}:mention:holder_name:1".format(
        ref_catalog["acct_aegean"]["record_version_id"]
    )
    _require(
        any(
            edge["predicate"] == "HOLDER_IDENTITY_DEPENDS_ON"
            and edge["to_node_id"] == holder_resolution_node
            for edge in structuring_dag["edges"]
        ),
        "structuring DAG must include the Aegean holder-name resolution",
    )

    comms_dag = next(
        dag
        for dag in dags
        if any(node.get("object_key") == "comms_before_transfer" for node in dag["nodes"])
    )
    expected_event_nodes = {
        "event:email:eM1",
        "event:same-event:c01:X-204:reconciliation@1",
        "event:same-event:c02:X-205:reconciliation@1",
        "event:call:c06",
    }
    for event_node_id in expected_event_nodes:
        predicates = {
            edge["predicate"]
            for edge in comms_dag["edges"]
            if edge["from_node_id"] == event_node_id
        }
        _require(
            {"ENDPOINT_RESOLVED_BY", "ENDPOINT_ATTRIBUTED_VIA"} <= predicates,
            f"counted event lacks transitive endpoint attribution: {event_node_id}",
        )

    must_fire = ground_truth["screening_outcomes"]["must_fire"]
    comms_screen = next(item for item in must_fire if item["rule"] == "comms_before_transfer")
    _require(
        all(item["provenance_dag_id"] in dag_ids for item in must_fire),
        "screening DAG reference missing",
    )
    _require(len(comms_screen["canonical_events"]) == 4, "comms screen must count four events")
    _require(
        [event["source_records"] for event in comms_screen["canonical_events"]]
        == [["eM1"], ["c01", "X-204"], ["c02", "X-205"], ["c06"]],
        "comms screen event collapse differs",
    )
