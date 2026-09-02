"""Build the canonical entity catalog referenced by every ground-truth layer."""

from typing import Any

from dataset.core.fixtures import DEVICES, PHONES


def build_entities() -> list[dict[str, Any]]:
    entity_specs = [
        ("ent_person_mavridis_a", "PERSON", "Alexandros Mavridis"),
        ("ent_person_mavridis_d", "PERSON", "Dimitris Mavridis"),
        ("ent_person_rossi", "PERSON", "K. Rossi"),
        ("ent_person_sofia", "PERSON", "Sofia Andreou"),
        ("ent_org_aegean", "ORGANIZATION", "Aegean Trade OE"),
        ("ent_org_meridian", "ORGANIZATION", "Meridian Consulting Ltd"),
        ("ent_org_ionian", "ORGANIZATION", "Ionian Supplies IKE"),
        ("ent_org_logistiki_b1", "ORGANIZATION", "Logistiki Attikis"),
        ("ent_phone_pa", "PHONE", PHONES["pa"]),
        ("ent_phone_pr", "PHONE", PHONES["pr"]),
        ("ent_phone_pd", "PHONE", PHONES["pd"]),
        ("ent_phone_n2", "PHONE", PHONES["n2"]),
        ("ent_phone_aegean", "PHONE", PHONES["aegean"]),
        ("ent_phone_b1", "PHONE", PHONES["b1"]),
        ("ent_phone_u1", "PHONE", PHONES["u1"]),
        ("ent_email_rossi", "EMAIL_ADDRESS", "k.rossi@aegeantrade.example"),
        ("ent_device_pa", "DEVICE", DEVICES["pa"]),
        ("ent_device_pr", "DEVICE", DEVICES["pr"]),
        ("ent_account_pa", "FINANCIAL_ACCOUNT", "acct_pa"),
        ("ent_account_pr", "FINANCIAL_ACCOUNT", "acct_pr"),
        ("ent_account_pd", "FINANCIAL_ACCOUNT", "acct_pd"),
        ("ent_account_aegean", "FINANCIAL_ACCOUNT", "acct_aegean"),
        ("ent_account_meridian", "FINANCIAL_ACCOUNT", "acct_meridian"),
        ("ent_account_ionian", "FINANCIAL_ACCOUNT", "acct_ionian"),
        ("ent_invoice_inv2231", "REFERENCE", "INV-2231"),
        ("ent_person_vasileiou_n1", "PERSON", "Elena Vasileiou"),
        ("ent_person_papadakis_n2", "PERSON", "G. Papadakis"),
        ("ent_person_mavridou_n8", "PERSON", "Alexandra Mavridou"),
    ]
    return [
        {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "canonical_label": label,
            "status": "confirmed",
        }
        for entity_id, entity_type, label in entity_specs
    ]
