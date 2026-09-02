"""Build resolution-decision records: identity/asset resolutions with cited evidence."""

from typing import Any

from dataset.core.constants import GENERATED_AT, POLICY_VERSION
from dataset.core.fixtures import DEVICES, PHONES
from dataset.core.state import _tr


def build_resolution_decisions(mention_resolutions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolution_decisions: list[dict[str, Any]] = [
        {
            "resolution_id": "res_phone_pa",
            "entity_id": "ent_phone_pa",
            "entity_type": "PHONE",
            "raw_values": ["+30 697 123 4567", "697 123 4567", "306971234567", "+306971234567"],
            "normalized_value": PHONES["pa"],
            "resolution_status": "confirmed",
            "method": "e164_exact",
            "supporting_records": ["R-01", "eM1", "c01", "X-204", "c13", "X-208"],
        },
        {
            "resolution_id": "res_phone_pr",
            "entity_id": "ent_phone_pr",
            "entity_type": "PHONE",
            "raw_values": ["694 987 6543", "6949876543", "306949876543", "+306949876543"],
            "normalized_value": PHONES["pr"],
            "resolution_status": "confirmed",
            "method": "e164_exact",
            "supporting_records": ["R-02", "eM2", "c01", "X-204"],
        },
        {
            "resolution_id": "res_phone_pd",
            "entity_id": "ent_phone_pd",
            "entity_type": "PHONE",
            "raw_values": ["+30 691 222 3344", "306912223344"],
            "normalized_value": PHONES["pd"],
            "resolution_status": "confirmed",
            "method": "e164_exact",
            "supporting_records": ["R-01", "c05", "c11"],
        },
        {
            "resolution_id": "res_phone_n2",
            "entity_id": "ent_phone_n2",
            "entity_type": "PHONE",
            "raw_values": ["+30 693 000 0102", "306930000102"],
            "normalized_value": PHONES["n2"],
            "resolution_status": "confirmed",
            "method": "e164_exact",
            "supporting_records": ["eM6", "c14"],
        },
        {
            "resolution_id": "res_phone_aegean",
            "entity_id": "ent_phone_aegean",
            "entity_type": "PHONE",
            "raw_values": ["+30 210 445 5667", "302104455667"],
            "normalized_value": PHONES["aegean"],
            "resolution_status": "confirmed",
            "method": "e164_exact",
            "supporting_records": ["R-03", "c08"],
        },
        {
            "resolution_id": "res_phone_b1",
            "entity_id": "ent_phone_b1",
            "entity_type": "PHONE",
            "raw_values": ["+30 210 111 2233", "302101112233"],
            "normalized_value": PHONES["b1"],
            "resolution_status": "confirmed",
            "method": "e164_exact",
            "supporting_records": ["eM5", "c11"],
        },
        {
            "resolution_id": "res_device_pa",
            "entity_id": "ent_device_pa",
            "entity_type": "DEVICE",
            "raw_values": [DEVICES["pa"]],
            "normalized_value": DEVICES["pa"],
            "resolution_status": "confirmed",
            "method": "exact_same_type_asset",
            "supporting_records": ["c01", "X-204", "c13", "X-208"],
        },
        {
            "resolution_id": "res_device_pr",
            "entity_id": "ent_device_pr",
            "entity_type": "DEVICE",
            "raw_values": [DEVICES["pr"]],
            "normalized_value": DEVICES["pr"],
            "resolution_status": "confirmed",
            "method": "exact_same_type_asset",
            "supporting_records": ["c08", "c09"],
        },
        {
            "resolution_id": "res_name_mavridis_a",
            "entity_id": "ent_person_mavridis_a",
            "entity_type": "PERSON",
            "raw_values": [
                "Alexandros Mavridis",
                "A. Mavridis",
                _tr("Α. Μαυρίδης", "Alexandros Mavridis"),
                "Alex",
            ],
            "normalized_value": "Alexandros Mavridis",
            "resolution_status": "confirmed",
            "method": "explicit_alias_plus_independent_corroboration",
            "supporting_records": ["R-01", "eM1", "acct_pa"],
        },
        {
            "resolution_id": "res_name_rossi",
            "entity_id": "ent_person_rossi",
            "entity_type": "PERSON",
            "raw_values": ["K. Rossi", _tr("Κ. Ρόσση", "Katherine Rossi")],
            "normalized_value": "K. Rossi",
            "resolution_status": "confirmed",
            "method": "explicit_alias_plus_independent_corroboration",
            "supporting_records": ["R-02", "eM2", "acct_pr"],
        },
    ]
    mention_ids_by_entity: dict[str, list[str]] = {}
    for resolution in mention_resolutions:
        mention_ids_by_entity.setdefault(resolution["entity_id"], []).append(
            resolution["mention_id"]
        )
    for decision in resolution_decisions:
        decision["supporting_mention_ids"] = sorted(
            mention_ids_by_entity.get(decision["entity_id"], [])
        )
        decision["policy_version"] = POLICY_VERSION
        decision["decided_by"] = "fixture_resolution_policy"
        decision["decided_at"] = GENERATED_AT
    return resolution_decisions
