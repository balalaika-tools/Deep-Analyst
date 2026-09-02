"""Build the observable-entity mentions and resolutions ground-truth layer."""

from typing import Any

from dataset.core.constants import GENERATED_AT, POLICY_VERSION
from dataset.core.fixtures import DEVICES, PHONES
from dataset.core.state import _tr
from dataset.core.util import _require
from dataset.provenance import _field_locator, _source_refs, _text_span_locator


def build_mentions_and_resolutions(
    case_id: str,
    ref_catalog: dict[str, dict[str, Any]],
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    phone_entities = {
        PHONES["pa"]: "ent_phone_pa",
        PHONES["pr"]: "ent_phone_pr",
        PHONES["pd"]: "ent_phone_pd",
        PHONES["n2"]: "ent_phone_n2",
        PHONES["aegean"]: "ent_phone_aegean",
        PHONES["b1"]: "ent_phone_b1",
        PHONES["u1"]: "ent_phone_u1",
    }
    device_entities = {
        DEVICES["pa"]: "ent_device_pa",
        DEVICES["pr"]: "ent_device_pr",
    }
    mentions: list[dict[str, Any]] = []
    resolutions: list[dict[str, Any]] = []
    source_refs_by_id: dict[str, dict[str, Any]] = {}

    def add(
        record_id: str,
        field: str,
        entity_type: str,
        raw_value: str,
        normalized_value: str,
        entity_id: str,
        *,
        span: str | None = None,
        extraction_method: str = "deterministic_field",
        extraction_quality: str = "rule_validated",
        resolution_status: str = "confirmed",
        resolution_method: str = "exact_same_type_asset",
    ) -> str:
        record_version_id = ref_catalog[record_id]["record_version_id"]
        mention_id = f"{record_version_id}:mention:{field}:1"
        logical_record = ref_catalog[record_id]["_logical_record"]
        if extraction_quality == "span_verified":
            if span and span.startswith("header:From"):
                locator = _text_span_locator(logical_record, "headers.From", raw_value)
            elif span and span.startswith("header:To"):
                locator = _text_span_locator(logical_record, "headers.To", raw_value)
            else:
                locator = _text_span_locator(logical_record, "body", raw_value)
        else:
            locator = _field_locator(logical_record, field)
        source_ref = _source_refs(ref_catalog, [record_id], {record_id: locator})[0]
        source_refs_by_id[source_ref["source_ref_id"]] = source_ref
        mentions.append(
            {
                "mention_id": mention_id,
                "case_id": case_id,
                "record_version_id": record_version_id,
                "entity_type": entity_type,
                "raw_value": raw_value,
                "normalized_value": normalized_value,
                "source_ref_id": source_ref["source_ref_id"],
                "span": locator,
                "extraction_method": extraction_method,
                "extraction_quality": extraction_quality,
            }
        )
        resolutions.append(
            {
                "mention_id": mention_id,
                "entity_id": entity_id,
                "resolution_status": resolution_status,
                "method": resolution_method,
                "policy_version": POLICY_VERSION,
                "decided_by": "fixture_ground_truth",
                "decided_at": GENERATED_AT,
            }
        )
        return mention_id

    for row in cdr:
        for field in ["subscriber_msisdn", "calling_msisdn", "called_msisdn"]:
            normalized = "+" + row[field]
            entity_id = phone_entities.get(normalized)
            if entity_id:
                add(row["record_id"], field, "PHONE", row[field], normalized, entity_id)
        if row["imei"] in device_entities:
            add(
                row["record_id"],
                "imei",
                "DEVICE",
                row["imei"],
                row["imei"],
                device_entities[row["imei"]],
            )

    for row in extraction:
        for field in ["subscriber_msisdn", "peer"]:
            normalized = row[field]
            entity_id = phone_entities.get(normalized)
            if entity_id:
                add(row["msg_id"], field, "PHONE", row[field], normalized, entity_id)
        if row["imei"] in device_entities:
            add(
                row["msg_id"],
                "imei",
                "DEVICE",
                row["imei"],
                row["imei"],
                device_entities[row["imei"]],
            )

    manual_mentions = [
        (
            "R-01",
            "body.name_mavridis_a",
            "PERSON",
            "A. Mavridis",
            "Alexandros Mavridis",
            "ent_person_mavridis_a",
            "body:span[A. Mavridis]",
            "span_verified",
            "explicit_alias_assertion",
        ),
        (
            "R-01",
            "body.name_mavridis_greek",
            "PERSON",
            _tr("Α. Μαυρίδης", "Alexandros Mavridis"),
            "Alexandros Mavridis",
            "ent_person_mavridis_a",
            _tr("body:span[Α. Μαυρίδης]", "body:span[Alexandros Mavridis]"),
            "span_verified",
            "explicit_alias_assertion",
        ),
        (
            "R-01",
            "body.alias_alex",
            "PERSON",
            "Alex",
            "Alexandros Mavridis",
            "ent_person_mavridis_a",
            "body:span[Alex]",
            "span_verified",
            "explicit_alias_assertion",
        ),
        (
            "R-01",
            "body.phone_pa",
            "PHONE",
            "+30 697 123 4567",
            PHONES["pa"],
            "ent_phone_pa",
            "body:span[+30 697 123 4567]",
            "span_verified",
            "e164_exact",
        ),
        (
            "R-01",
            "body.person_pd",
            "PERSON",
            "Dimitris Mavridis",
            "Dimitris Mavridis",
            "ent_person_mavridis_d",
            "body:span[Dimitris Mavridis]",
            "span_verified",
            "document_identity_statement",
        ),
        (
            "R-01",
            "body.phone_pd",
            "PHONE",
            "+30 691 222 3344",
            PHONES["pd"],
            "ent_phone_pd",
            "body:span[+30 691 222 3344]",
            "span_verified",
            "e164_exact",
        ),
        (
            "R-01",
            "body.person_sofia",
            "PERSON",
            "Sofia Andreou",
            "Sofia Andreou",
            "ent_person_sofia",
            "body:span[Sofia Andreou]",
            "span_verified",
            "document_identity_statement",
        ),
        (
            "R-02",
            "body.person_rossi",
            "PERSON",
            "K. Rossi",
            "K. Rossi",
            "ent_person_rossi",
            "body:span[K. Rossi]",
            "span_verified",
            "explicit_alias_assertion",
        ),
        (
            "R-02",
            "body.person_rossi_greek",
            "PERSON",
            _tr("Κ. Ρόσση", "Katherine Rossi"),
            "K. Rossi",
            "ent_person_rossi",
            _tr("body:span[Κ. Ρόσση]", "body:span[Katherine Rossi]"),
            "span_verified",
            "explicit_alias_assertion",
        ),
        (
            "R-02",
            "body.phone_pr",
            "PHONE",
            "694 987 6543",
            PHONES["pr"],
            "ent_phone_pr",
            "body:span[694 987 6543]",
            "span_verified",
            "e164_exact",
        ),
        (
            "R-02",
            "body.person_sofia",
            "PERSON",
            "Sofia Andreou",
            "Sofia Andreou",
            "ent_person_sofia",
            "body:span[Sofia Andreou]",
            "span_verified",
            "document_identity_statement",
        ),
        (
            "R-03",
            "body.phone_aegean",
            "PHONE",
            "+30 210 445 5667",
            PHONES["aegean"],
            "ent_phone_aegean",
            "body:span[+30 210 445 5667]",
            "span_verified",
            "e164_exact",
        ),
        (
            "eM1",
            "headers.From.display_name",
            "PERSON",
            "A. Mavridis",
            "Alexandros Mavridis",
            "ent_person_mavridis_a",
            "header:From.display_name",
            "span_verified",
            "alias_plus_phone_corroboration",
        ),
        (
            "eM1",
            "headers.To.address",
            "EMAIL_ADDRESS",
            "k.rossi@aegeantrade.example",
            "k.rossi@aegeantrade.example",
            "ent_email_rossi",
            "header:To.address",
            "span_verified",
            "email_address_exact",
        ),
        (
            "eM1",
            "body.phone_pa",
            "PHONE",
            "697 123 4567",
            PHONES["pa"],
            "ent_phone_pa",
            "body:span[697 123 4567]",
            "span_verified",
            "e164_exact",
        ),
        (
            "eM2",
            "headers.From.display_name",
            "PERSON",
            "K. Rossi",
            "K. Rossi",
            "ent_person_rossi",
            "header:From.display_name",
            "span_verified",
            "header_identity",
        ),
        (
            "eM2",
            "headers.From.address",
            "EMAIL_ADDRESS",
            "k.rossi@aegeantrade.example",
            "k.rossi@aegeantrade.example",
            "ent_email_rossi",
            "header:From.address",
            "span_verified",
            "email_address_exact",
        ),
        (
            "eM2",
            "body.signature_name",
            "PERSON",
            "K. Rossi",
            "K. Rossi",
            "ent_person_rossi",
            "body:span[K. Rossi]",
            "span_verified",
            "signature_identity",
        ),
        (
            "eM2",
            "body.phone_pr",
            "PHONE",
            "6949876543",
            PHONES["pr"],
            "ent_phone_pr",
            "body:span[6949876543]",
            "span_verified",
            "e164_exact",
        ),
        (
            "eM5",
            "headers.To.display_name",
            "PERSON",
            "Dimitris Mavridis",
            "Dimitris Mavridis",
            "ent_person_mavridis_d",
            "header:To.display_name",
            "span_verified",
            "header_identity",
        ),
        (
            "eM5",
            "body.phone_b1",
            "PHONE",
            "+30 210 111 2233",
            PHONES["b1"],
            "ent_phone_b1",
            "body:span[+30 210 111 2233]",
            "span_verified",
            "e164_exact",
        ),
        (
            "eM6",
            "body.person_n2",
            "PERSON",
            "G. Papadakis",
            "G. Papadakis",
            "ent_person_papadakis_n2",
            "body:span[G. Papadakis]",
            "span_verified",
            "receipt_identity_statement",
        ),
        (
            "eM6",
            "body.phone_n2",
            "PHONE",
            "+30 693 000 0102",
            PHONES["n2"],
            "ent_phone_n2",
            "body:span[+30 693 000 0102]",
            "span_verified",
            "e164_exact",
        ),
        (
            "X-208",
            "body.person_sofia",
            "PERSON",
            _tr("η Σοφία είμαι", "this is Sofia"),
            "Sofia Andreou",
            "ent_person_sofia",
            _tr("body:span[η Σοφία είμαι]", "body:span[this is Sofia]"),
            "span_verified",
            "self_identification_plus_corroboration",
        ),
        (
            "acct_pa",
            "holder_name",
            "PERSON",
            "Alexandros Mavridis",
            "Alexandros Mavridis",
            "ent_person_mavridis_a",
            "field:holder_name",
            "rule_validated",
            "structured_account_identity",
        ),
        (
            "acct_pr",
            "holder_name",
            "PERSON",
            "K. Rossi",
            "K. Rossi",
            "ent_person_rossi",
            "field:holder_name",
            "rule_validated",
            "structured_account_identity",
        ),
        (
            "acct_pd",
            "holder_name",
            "PERSON",
            "Dimitris Mavridis",
            "Dimitris Mavridis",
            "ent_person_mavridis_d",
            "field:holder_name",
            "rule_validated",
            "structured_account_identity",
        ),
        (
            "nA02",
            "holder_name",
            "PERSON",
            "G. Papadakis",
            "G. Papadakis",
            "ent_person_papadakis_n2",
            "field:holder_name",
            "rule_validated",
            "structured_account_identity",
        ),
        (
            "acct_aegean",
            "holder_name",
            "ORGANIZATION",
            "Aegean Trade OE",
            "Aegean Trade OE",
            "ent_org_aegean",
            "field:holder_name",
            "rule_validated",
            "structured_account_identity",
        ),
        (
            "nA09",
            "holder_name",
            "PERSON",
            "Alexandra Mavridou",
            "Alexandra Mavridou",
            "ent_person_mavridou_n8",
            "field:holder_name",
            "rule_validated",
            "structured_account_identity",
        ),
        (
            "N-D2",
            "body.person_n8",
            "PERSON",
            "Alexandra Mavridou",
            "Alexandra Mavridou",
            "ent_person_mavridou_n8",
            "body:span[Alexandra Mavridou]",
            "span_verified",
            "document_identity_statement",
        ),
    ]
    manual_ids: dict[tuple[str, str], str] = {}
    for (
        record_id,
        field,
        entity_type,
        raw_value,
        normalized_value,
        entity_id,
        span,
        quality,
        method,
    ) in manual_mentions:
        resolution_status = "proposed" if record_id == "N-D2" else "confirmed"
        resolution_method = "name_only_cross_source" if record_id == "N-D2" else method
        manual_ids[(record_id, field)] = add(
            record_id,
            field,
            entity_type,
            raw_value,
            normalized_value,
            entity_id,
            span=span,
            extraction_method="verified_source_span"
            if quality == "span_verified"
            else "structured_field",
            extraction_quality=quality,
            resolution_status=resolution_status,
            resolution_method=resolution_method,
        )

    aegean_iban = ref_catalog["acct_aegean"]["_logical_record"]["iban"]
    add(
        "acct_aegean",
        "iban",
        "FINANCIAL_ACCOUNT",
        aegean_iban,
        aegean_iban,
        "ent_account_aegean",
        resolution_method="exact_same_type_asset",
    )
    for txn_id in ["t_85", "t_86", "t_88"]:
        add(
            txn_id,
            "debtor_iban",
            "FINANCIAL_ACCOUNT",
            aegean_iban,
            aegean_iban,
            "ent_account_aegean",
            resolution_method="exact_same_type_asset",
        )

    candidates = [
        {
            "candidate_id": "actor-candidate:ent_person_mavridou_n8:ent_person_mavridis_a:actor-resolution-score@1",
            "case_id": case_id,
            "left_entity_id": "ent_person_mavridou_n8",
            "right_entity_id": "ent_person_mavridis_a",
            "resolution_status": "proposed",
            "expected_action": "review_then_reject",
            "latent_match": False,
            "method": "near_name_transliteration_only",
            "score_semantics": "ranking_feature_not_probability",
            "supporting_mention_ids": [
                manual_ids[("nA09", "holder_name")],
                manual_ids[("N-D2", "body.person_n8")],
                manual_ids[("acct_pa", "holder_name")],
            ],
            "conflicting_features": [
                "different account",
                "different phone/context",
                "no corroborating identifier",
            ],
            "policy_version": POLICY_VERSION,
            "decided_by": "fixture_policy",
            "decided_at": GENERATED_AT,
            "merge_allowed": False,
        }
    ]
    _require(
        len({mention["mention_id"] for mention in mentions}) == len(mentions),
        "mention IDs must be unique",
    )
    return mentions, resolutions, candidates, list(source_refs_by_id.values())
