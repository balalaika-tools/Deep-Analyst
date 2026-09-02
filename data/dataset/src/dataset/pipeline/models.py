"""Build and validate every in-memory model used by one dataset release."""

import random
from typing import Any

from dataset.core.constants import DEFAULT_LOCALE
from dataset.core.state import _activate_locale
from dataset.core.util import _case_namespace, _require
from dataset.ground_truth import build_ground_truth
from dataset.provenance import build_source_ref_catalog
from dataset.quarantine import build_previews, build_quarantine
from dataset.sources import (
    build_accounts,
    build_cdr,
    build_documents,
    build_emails,
    build_extraction,
    build_policy,
    build_transactions,
)
from dataset.validation import validate_models


def _build_dataset_models(seed: int, locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    """Build and validate every in-memory model used by a dataset release."""
    _require(type(seed) is int, "rng seed must be an integer")
    _activate_locale(locale)
    case_id, variant_id = _case_namespace(seed)
    rng = random.Random(seed)
    accounts = build_accounts(case_id)
    transactions = build_transactions(case_id, accounts)
    cdr = build_cdr(case_id, rng)
    extraction = build_extraction(case_id)
    emails = build_emails(case_id)
    documents = build_documents(case_id)
    policy = build_policy()
    ref_catalog = build_source_ref_catalog(
        case_id, cdr, extraction, emails, accounts, transactions, documents
    )
    ground_truth = build_ground_truth(case_id, ref_catalog, cdr, extraction)
    quarantine_files, quarantine_expected = build_quarantine(case_id)
    previews = build_previews(case_id, cdr, extraction, emails, accounts, transactions, documents)

    validate_models(
        case_id,
        cdr,
        extraction,
        emails,
        accounts,
        transactions,
        documents,
        policy,
        ground_truth,
    )
    return {
        "seed": seed,
        "case_id": case_id,
        "variant_id": variant_id,
        "cdr": cdr,
        "extraction": extraction,
        "emails": emails,
        "accounts": accounts,
        "transactions": transactions,
        "documents": documents,
        "policy": policy,
        "ground_truth": ground_truth,
        "quarantine_files": quarantine_files,
        "quarantine_expected": quarantine_expected,
        "previews": previews,
    }
