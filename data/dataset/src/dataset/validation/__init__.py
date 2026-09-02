"""Validate generated models: timestamps, source refs, provenance DAGs, and
cross-source reconciliation."""

from typing import Any

from dataset.provenance import build_source_ref_catalog
from dataset.validation.communications import validate_cdr_structure, validate_reconciliation
from dataset.validation.financial import validate_accounts_and_transactions
from dataset.validation.ground_truth import validate_golden_questions, validate_observable_layer
from dataset.validation.narrative import validate_documents, validate_emails
from dataset.validation.structure import (
    validate_calendar_facts,
    validate_policy,
    validate_record_counts,
    validate_stable_ids_and_case_namespace,
)

__all__ = ["validate_models"]


def validate_models(
    case_id: str,
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
    emails: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    policy: dict[str, Any],
    ground_truth: dict[str, Any],
) -> None:
    validate_record_counts(cdr, extraction, emails, accounts, transactions, documents)
    source_ids = validate_stable_ids_and_case_namespace(
        case_id, cdr, extraction, emails, accounts, transactions, documents
    )
    validate_accounts_and_transactions(accounts, transactions)
    validate_cdr_structure(cdr, extraction)
    validate_reconciliation(cdr, extraction, transactions)
    validate_emails(emails)
    validate_documents(documents)
    validate_policy(policy)
    validate_golden_questions(ground_truth)

    validation_catalog = build_source_ref_catalog(
        case_id, cdr, extraction, emails, accounts, transactions, documents
    )
    validate_observable_layer(case_id, validation_catalog, ground_truth, source_ids)
    validate_calendar_facts()
