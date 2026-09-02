"""Build the provenance-DAG ground-truth layer."""

from collections.abc import Sequence
from typing import Any

from dataset.core.constants import POLICY_VERSION
from dataset.core.util import _json_bytes, _require, _sha256
from dataset.provenance.catalog import _source_refs
from dataset.provenance.locators import _field_set_locator
from dataset.sources import build_policy


def build_provenance_dags(
    case_id: str,
    ref_catalog: dict[str, dict[str, Any]],
    assertions: list[dict[str, Any]],
    event_crosswalks: list[dict[str, Any]],
    entity_mentions: list[dict[str, Any]],
    mention_resolutions: list[dict[str, Any]],
    resolution_decisions: list[dict[str, Any]],
    known_source_refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs_by_id = {ref["source_ref_id"]: ref for ref in known_source_refs}
    mention_by_id = {mention["mention_id"]: mention for mention in entity_mentions}
    mention_resolution_by_id = {
        resolution["mention_id"]: resolution for resolution in mention_resolutions
    }

    def exact_ref(record_id: str, fields: Sequence[str]) -> dict[str, Any]:
        locator = _field_set_locator(ref_catalog[record_id]["_logical_record"], fields)
        ref = _source_refs(ref_catalog, [record_id], {record_id: locator})[0]
        refs_by_id[ref["source_ref_id"]] = ref
        return ref

    def assertion(subject: str, predicate: str, obj: str) -> dict[str, Any]:
        return next(
            item
            for item in assertions
            if item["subject_entity_id"] == subject
            and item["predicate"] == predicate
            and item["object_entity_id"] == obj
        )

    def decision(resolution_id: str) -> dict[str, Any]:
        return next(item for item in resolution_decisions if item["resolution_id"] == resolution_id)

    def find_mention(record_id: str, field: str) -> dict[str, Any]:
        mention_id = "{}:mention:{}:1".format(ref_catalog[record_id]["record_version_id"], field)
        _require(mention_id in mention_by_id, f"provenance mention missing: {mention_id}")
        return mention_by_id[mention_id]

    policy_node = {
        "node_id": f"policy:{POLICY_VERSION}",
        "node_type": "policy",
        "policy_version": POLICY_VERSION,
        "artifact_path": f"policies/{POLICY_VERSION}.json",
        "artifact_hash": _sha256(_json_bytes(build_policy(), pretty=True)),
    }

    def source_node(ref: dict[str, Any]) -> dict[str, Any]:
        return {
            "node_id": "source:{}".format(ref["source_ref_id"]),
            "node_type": "source_ref",
            "source_ref_id": ref["source_ref_id"],
        }

    def attach_mention_resolution(
        add_node: Any,
        add_edge: Any,
        parent_node_id: str,
        edge_predicate: str,
        source_mention: dict[str, Any],
    ) -> str:
        resolution = mention_resolution_by_id[source_mention["mention_id"]]
        resolution_node_id = "mention-resolution:{}".format(source_mention["mention_id"])
        add_node(
            {
                "node_id": resolution_node_id,
                "node_type": "mention_resolution",
                "mention_id": source_mention["mention_id"],
                "entity_id": resolution["entity_id"],
            }
        )
        add_edge(parent_node_id, edge_predicate, resolution_node_id)
        ref = refs_by_id[source_mention["source_ref_id"]]
        add_node(source_node(ref))
        add_edge(resolution_node_id, "SUPPORTED_BY", source_node(ref)["node_id"])
        return resolution_node_id

    def build_dag(
        dag_id: str,
        root: dict[str, Any],
        coverage: dict[str, Any],
    ) -> tuple[dict[str, Any], Any, Any]:
        nodes: dict[str, dict[str, Any]] = {
            root["node_id"]: root,
            policy_node["node_id"]: policy_node,
        }
        edges: list[dict[str, str]] = []

        def add_node(node: dict[str, Any]) -> None:
            nodes[node["node_id"]] = node

        def add_edge(source: str, predicate: str, target: str) -> None:
            edges.append({"from_node_id": source, "predicate": predicate, "to_node_id": target})

        add_edge(root["node_id"], "EVALUATED_UNDER", policy_node["node_id"])
        dag = {
            "dag_id": dag_id,
            "case_id": case_id,
            "root_node_id": root["node_id"],
            "nodes": nodes,
            "edges": edges,
            "coverage": coverage,
            "complete": True,
        }
        return dag, add_node, add_edge

    # Structuring screen DAG.
    structuring_root_id = f"result:structuring_sub_threshold:ent_org_aegean:{POLICY_VERSION}"
    structuring, add_struct_node, add_struct_edge = build_dag(
        f"dag:screen:structuring_sub_threshold:ent_org_aegean:{POLICY_VERSION}",
        {
            "node_id": structuring_root_id,
            "node_type": "screening_result",
            "object_key": "structuring_sub_threshold",
            "subject_entity_id": "ent_org_aegean",
        },
        {
            "window": "2026-03-03T00:00:00Z..2026-03-05T23:59:59Z",
            "sources": ["bank"],
            "exhaustive": True,
        },
    )
    iban_resolution_ids = {
        ref_catalog["t_85"]["record_version_id"],
        ref_catalog["t_86"]["record_version_id"],
        ref_catalog["t_88"]["record_version_id"],
        ref_catalog["acct_aegean"]["record_version_id"],
    }
    iban_mentions = [
        mention
        for mention in entity_mentions
        if mention["record_version_id"] in iban_resolution_ids
        and mention["entity_type"] == "FINANCIAL_ACCOUNT"
    ]
    iban_mentions_by_record = {mention["record_version_id"]: mention for mention in iban_mentions}
    for txn_id in ["t_85", "t_86", "t_88"]:
        txn_node_id = f"transaction:{txn_id}"
        add_struct_node(
            {
                "node_id": txn_node_id,
                "node_type": "canonical_transaction",
                "record_version_id": ref_catalog[txn_id]["record_version_id"],
            }
        )
        add_struct_edge(structuring_root_id, "TRIGGERED_BY", txn_node_id)
        txn_ref = exact_ref(
            txn_id,
            [
                "booking_ts_utc",
                "debtor_iban",
                "creditor_iban",
                "amount_text",
                "currency",
                "status",
            ],
        )
        add_struct_node(source_node(txn_ref))
        add_struct_edge(txn_node_id, "SUPPORTED_BY", source_node(txn_ref)["node_id"])
        mention = iban_mentions_by_record[ref_catalog[txn_id]["record_version_id"]]
        mention_resolution = mention_resolution_by_id[mention["mention_id"]]
        resolution_node_id = "mention-resolution:{}".format(mention["mention_id"])
        add_struct_node(
            {
                "node_id": resolution_node_id,
                "node_type": "mention_resolution",
                "mention_id": mention["mention_id"],
                "entity_id": mention_resolution["entity_id"],
            }
        )
        add_struct_edge(txn_node_id, "DEBTOR_ACCOUNT_RESOLVED_BY", resolution_node_id)
        source_ref = refs_by_id[mention["source_ref_id"]]
        add_struct_node(source_node(source_ref))
        add_struct_edge(resolution_node_id, "SUPPORTED_BY", source_node(source_ref)["node_id"])

    account_assertion = assertion("ent_account_aegean", "HELD_BY", "ent_org_aegean")
    account_assertion_node = "assertion:{}".format(account_assertion["assertion_id"])
    add_struct_node(
        {
            "node_id": account_assertion_node,
            "node_type": "relationship_assertion",
            "assertion_id": account_assertion["assertion_id"],
        }
    )
    add_struct_edge(structuring_root_id, "GROUPED_AS_SUBJECT", account_assertion_node)
    for ref in account_assertion["supporting_refs"]:
        refs_by_id[ref["source_ref_id"]] = ref
        add_struct_node(source_node(ref))
        add_struct_edge(account_assertion_node, "SUPPORTED_BY", source_node(ref)["node_id"])
    account_mention = iban_mentions_by_record[ref_catalog["acct_aegean"]["record_version_id"]]
    account_resolution_node = "mention-resolution:{}".format(account_mention["mention_id"])
    add_struct_node(
        {
            "node_id": account_resolution_node,
            "node_type": "mention_resolution",
            "mention_id": account_mention["mention_id"],
            "entity_id": "ent_account_aegean",
        }
    )
    add_struct_edge(account_assertion_node, "ACCOUNT_IDENTITY_DEPENDS_ON", account_resolution_node)
    account_mention_ref = refs_by_id[account_mention["source_ref_id"]]
    add_struct_node(source_node(account_mention_ref))
    add_struct_edge(
        account_resolution_node, "SUPPORTED_BY", source_node(account_mention_ref)["node_id"]
    )
    attach_mention_resolution(
        add_struct_node,
        add_struct_edge,
        account_assertion_node,
        "HOLDER_IDENTITY_DEPENDS_ON",
        find_mention("acct_aegean", "holder_name"),
    )

    # Communications-before-transfer screen DAG.
    comms_root_id = (
        f"result:comms_before_transfer:ent_person_mavridis_a:ent_person_rossi:{POLICY_VERSION}"
    )
    comms, add_comms_node, add_comms_edge = build_dag(
        f"dag:screen:comms_before_transfer:ent_person_mavridis_a:ent_person_rossi:{POLICY_VERSION}",
        {
            "node_id": comms_root_id,
            "node_type": "screening_result",
            "object_key": "comms_before_transfer",
            "actor_pair": ["ent_person_mavridis_a", "ent_person_rossi"],
        },
        {
            "window": "2026-03-04T14:30:00Z..2026-03-05T14:30:00Z",
            "sources": ["cdr", "extraction", "email", "bank", "docs"],
            "exhaustive_structured_envelopes": True,
        },
    )
    transfer_node_id = "transaction:t_88"
    add_comms_node(
        {
            "node_id": transfer_node_id,
            "node_type": "canonical_transaction",
            "record_version_id": ref_catalog["t_88"]["record_version_id"],
        }
    )
    add_comms_edge(comms_root_id, "TRIGGERED_BY", transfer_node_id)
    transfer_ref = exact_ref(
        "t_88",
        ["booking_ts_utc", "debtor_iban", "creditor_iban", "amount_text", "currency", "status"],
    )
    add_comms_node(source_node(transfer_ref))
    add_comms_edge(transfer_node_id, "SUPPORTED_BY", source_node(transfer_ref)["node_id"])

    event_specs: list[dict[str, Any]] = [
        {
            "event_id": "email:eM1",
            "refs": [("eM1", ["headers.From", "headers.To", "headers.Date"])],
            "endpoint_mentions": [
                ("eM1", "headers.From.display_name"),
                ("eM1", "headers.To.address"),
                ("eM1", "body.phone_pa"),
            ],
            "endpoint_assertions": [
                ("ent_person_mavridis_a", "USES", "ent_phone_pa"),
                ("ent_person_rossi", "ASSOCIATED_WITH", "ent_email_rossi"),
            ],
        },
        {
            "event_id": "same-event:c01:X-204:reconciliation@1",
            "refs": [
                (
                    "c01",
                    ["calling_msisdn", "called_msisdn", "subscriber_msisdn", "imei", "ts_local"],
                ),
                ("X-204", ["subscriber_msisdn", "direction", "peer", "imei", "app", "ts_utc"]),
            ],
            "endpoint_mentions": [
                ("c01", "calling_msisdn"),
                ("c01", "called_msisdn"),
                ("X-204", "subscriber_msisdn"),
                ("X-204", "peer"),
            ],
            "endpoint_assertions": [
                ("ent_person_mavridis_a", "USES", "ent_phone_pa"),
                ("ent_person_rossi", "USES", "ent_phone_pr"),
            ],
        },
        {
            "event_id": "same-event:c02:X-205:reconciliation@1",
            "refs": [
                (
                    "c02",
                    ["calling_msisdn", "called_msisdn", "subscriber_msisdn", "imei", "ts_local"],
                ),
                ("X-205", ["subscriber_msisdn", "direction", "peer", "imei", "app", "ts_utc"]),
            ],
            "endpoint_mentions": [
                ("c02", "calling_msisdn"),
                ("c02", "called_msisdn"),
                ("X-205", "peer"),
                ("X-205", "subscriber_msisdn"),
            ],
            "endpoint_assertions": [
                ("ent_person_mavridis_a", "USES", "ent_phone_pa"),
                ("ent_person_rossi", "USES", "ent_phone_pr"),
            ],
        },
        {
            "event_id": "call:c06",
            "refs": [
                (
                    "c06",
                    [
                        "calling_msisdn",
                        "called_msisdn",
                        "subscriber_msisdn",
                        "ts_local",
                        "duration_s",
                    ],
                )
            ],
            "endpoint_mentions": [
                ("c06", "calling_msisdn"),
                ("c06", "called_msisdn"),
            ],
            "endpoint_assertions": [
                ("ent_person_mavridis_a", "USES", "ent_phone_pa"),
                ("ent_person_rossi", "USES", "ent_phone_pr"),
            ],
        },
    ]
    crosswalk_ids = {item["event_link_id"] for item in event_crosswalks}
    for event_spec in event_specs:
        event_id = event_spec["event_id"]
        node_id = f"event:{event_id}"
        node_type = "event_crosswalk" if event_id in crosswalk_ids else "canonical_event"
        event_node = {"node_id": node_id, "node_type": node_type, "event_id": event_id}
        if node_type == "event_crosswalk":
            event_node["event_link_id"] = event_id
        add_comms_node(event_node)
        add_comms_edge(comms_root_id, "COUNTED_EVENT", node_id)
        for record_id, fields in event_spec["refs"]:
            ref = exact_ref(record_id, fields)
            add_comms_node(source_node(ref))
            add_comms_edge(node_id, "SUPPORTED_BY", source_node(ref)["node_id"])
        for record_id, field in event_spec["endpoint_mentions"]:
            attach_mention_resolution(
                add_comms_node,
                add_comms_edge,
                node_id,
                "ENDPOINT_RESOLVED_BY",
                find_mention(record_id, field),
            )
        for subject, predicate, obj in event_spec["endpoint_assertions"]:
            endpoint_assertion = assertion(subject, predicate, obj)
            add_comms_edge(
                node_id,
                "ENDPOINT_ATTRIBUTED_VIA",
                "assertion:{}".format(endpoint_assertion["assertion_id"]),
            )

    endpoint_dependencies = [
        (
            assertion("ent_person_mavridis_a", "USES", "ent_phone_pa"),
            decision("res_name_mavridis_a"),
        ),
        (
            assertion("ent_person_rossi", "USES", "ent_phone_pr"),
            decision("res_name_rossi"),
        ),
        (
            assertion("ent_person_rossi", "ASSOCIATED_WITH", "ent_email_rossi"),
            decision("res_name_rossi"),
        ),
    ]
    added_actor_decision_nodes = set()
    for endpoint_assertion, actor_decision in endpoint_dependencies:
        assertion_node_id = "assertion:{}".format(endpoint_assertion["assertion_id"])
        add_comms_node(
            {
                "node_id": assertion_node_id,
                "node_type": "relationship_assertion",
                "assertion_id": endpoint_assertion["assertion_id"],
            }
        )
        add_comms_edge(comms_root_id, "ENDPOINT_ATTRIBUTION_DEPENDS_ON", assertion_node_id)
        for ref in endpoint_assertion["supporting_refs"]:
            refs_by_id[ref["source_ref_id"]] = ref
            add_comms_node(source_node(ref))
            add_comms_edge(assertion_node_id, "SUPPORTED_BY", source_node(ref)["node_id"])

        decision_node_id = "actor-resolution:{}".format(actor_decision["resolution_id"])
        add_comms_edge(
            assertion_node_id,
            "ACTOR_IDENTITY_DEPENDS_ON",
            decision_node_id,
        )
        if decision_node_id in added_actor_decision_nodes:
            continue
        added_actor_decision_nodes.add(decision_node_id)
        add_comms_node(
            {
                "node_id": decision_node_id,
                "node_type": "actor_resolution",
                "resolution_id": actor_decision["resolution_id"],
                "entity_id": actor_decision["entity_id"],
            }
        )
        add_comms_edge(comms_root_id, "ENDPOINT_ATTRIBUTION_DEPENDS_ON", decision_node_id)
        for mention_id in actor_decision["supporting_mention_ids"]:
            mention = mention_by_id[mention_id]
            resolution = mention_resolution_by_id[mention_id]
            resolution_node_id = f"mention-resolution:{mention_id}"
            add_comms_node(
                {
                    "node_id": resolution_node_id,
                    "node_type": "mention_resolution",
                    "mention_id": mention_id,
                    "entity_id": resolution["entity_id"],
                }
            )
            add_comms_edge(decision_node_id, "DERIVED_FROM", resolution_node_id)
            ref = refs_by_id[mention["source_ref_id"]]
            add_comms_node(source_node(ref))
            add_comms_edge(resolution_node_id, "SUPPORTED_BY", source_node(ref)["node_id"])

    # Freeze the mutable node dictionaries returned by build_dag.
    for dag in [structuring, comms]:
        dag["nodes"] = sorted(dag["nodes"].values(), key=lambda node: node["node_id"])
        dag["edges"] = sorted(
            dag["edges"],
            key=lambda edge: (edge["from_node_id"], edge["predicate"], edge["to_node_id"]),
        )
    return [structuring, comms], list(refs_by_id.values())
