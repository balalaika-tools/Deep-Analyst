"""Build the CDR/extraction event-crosswalk records used by the
comms-before-transfer screen."""

from typing import Any


def build_event_crosswalks(ref_catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    crosswalk_pairs = [
        ("c01", "X-204"),
        ("c02", "X-205"),
        ("c04", "X-206"),
        ("c10", "X-207"),
        ("c12", "X-303"),
        ("c13", "X-208"),
    ]
    return [
        {
            "event_link_id": f"same-event:{cdr_id}:{extraction_id}:reconciliation@1",
            "left_record_version_id": ref_catalog[cdr_id]["record_version_id"],
            "right_record_version_id": ref_catalog[extraction_id]["record_version_id"],
            "status": "confirmed",
            "rule_id": "cdr-extraction-sms",
            "rule_version": "reconciliation@1",
            "compared_fields": {
                "timestamp_delta_seconds": 0,
                "network_roles_equal": True,
                "compatible_device_or_subscriber": True,
            },
        }
        for cdr_id, extraction_id in crosswalk_pairs
    ]
