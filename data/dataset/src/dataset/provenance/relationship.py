"""Build a cited, hash-identified relationship assertion between two entities."""

from collections.abc import Sequence
from typing import Any

from dataset.core.constants import GENERATED_AT, POLICY_VERSION
from dataset.core.util import _json_bytes, _sha256
from dataset.provenance.catalog import _source_refs
from dataset.provenance.locators import _assertion_locator


def _relationship_assertion(
    case_id: str,
    ref_catalog: dict[str, dict[str, Any]],
    subject: str,
    predicate: str,
    obj: str,
    supports: Sequence[str],
    *,
    status: str,
    inference_strength: str,
    method: str,
    valid_from: str | None = None,
    valid_to: str | None = None,
    identity_status: str = "confirmed",
    support_locators: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    locators = {
        record_id: _assertion_locator(
            record_id,
            subject,
            predicate,
            obj,
            ref_catalog[record_id],
        )
        for record_id in supports
    }
    if support_locators:
        locators.update(support_locators)
    refs = _source_refs(ref_catalog, supports, locators)
    needs_review = status != "confirmed" or inference_strength == "inferred"
    payload = {
        "case_id": case_id,
        "subject_entity_id": subject,
        "predicate": predicate,
        "object_entity_id": obj,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "observed_at": GENERATED_AT,
        "method": method,
        "assertion_status": status,
        "supporting_refs": refs,
        "extraction_quality_by_ref": {
            ref["source_ref_id"]: (
                "needs_review"
                if needs_review
                else "span_verified"
                if ref["source_system"] in {"docs", "email"}
                else "rule_validated"
            )
            for ref in refs
        },
        "identity_status": identity_status,
        "source_reliability_by_ref": {
            ref["source_ref_id"]: ref_catalog[ref["source_record_id"]]["source_reliability"]
            for ref in refs
        },
        "inference_strength": inference_strength,
        "policy_version": POLICY_VERSION,
        "supersedes": None,
    }
    digest = _sha256(_json_bytes(payload))
    payload["assertion_id"] = f"rel:{subject}:{predicate}:{obj}:{digest}"
    return payload
