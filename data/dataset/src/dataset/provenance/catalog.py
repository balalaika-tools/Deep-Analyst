"""Build the source-reference catalog and cite exact records with a SourceRef."""

from collections.abc import Sequence
from typing import Any

from dataset.core import state
from dataset.core.constants import ACCOUNT_COLUMNS, TRANSACTION_COLUMNS
from dataset.core.util import (
    _cdr_lexemes,
    _global_record_id,
    _json_bytes,
    _ordered_row_hash,
    _record_hash,
    _sha256,
)


def build_source_ref_catalog(
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
    emails: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}

    def add(
        record_id: str,
        source: str,
        raw_path: str,
        content_hash: str,
        reliability: str,
        default_locator: str,
        logical_record: dict[str, Any],
    ) -> None:
        record_key = _global_record_id(source, record_id)
        catalog[record_id] = {
            "record_id": record_key,
            "source": source,
            "source_version": state.SOURCE_VERSIONS[source],
            "source_record_id": record_id,
            "record_version_id": f"{record_key}:{content_hash}",
            "raw_content_hash": content_hash,
            "raw_path": raw_path,
            "source_reliability": reliability,
            "default_field_or_span": default_locator,
            "_logical_record": logical_record,
        }

    for row in cdr:
        add(
            row["record_id"],
            "cdr",
            "raw/cdr.csv",
            _record_hash(_cdr_lexemes(row)),
            "unknown",
            "row:all_columns",
            _cdr_lexemes(row),
        )
    for row in extraction:
        add(
            row["msg_id"],
            "extraction",
            "raw/extraction.jsonl",
            _record_hash(row),
            "unknown",
            "object:all_fields",
            row,
        )
    for email in emails:
        semantic = {
            "email_id": email["email_id"],
            "headers": email["headers"],
            "body": email["body"],
        }
        add(
            email["email_id"],
            "email",
            "raw/emails/{}.eml".format(email["email_id"]),
            _record_hash(semantic),
            "unknown",
            "message:all_headers_and_body",
            semantic,
        )
    for row in accounts:
        add(
            row["account_id"],
            "bank",
            "raw/bank.sql",
            _ordered_row_hash(row, ACCOUNT_COLUMNS),
            "unknown",
            "account_row:all_columns",
            row,
        )
    for row in transactions:
        add(
            row["txn_id"],
            "bank",
            "raw/bank.sql",
            _ordered_row_hash(row, TRANSACTION_COLUMNS),
            "unknown",
            "transaction_row:all_columns",
            row,
        )
    for document in documents:
        semantic = {
            "document_id": document["document_id"],
            "front_matter": document["front_matter"],
            "body": document["body"],
        }
        add(
            document["document_id"],
            "docs",
            "raw/docs/{}.md".format(document["document_id"]),
            _record_hash(semantic),
            document["front_matter"]["source_reliability"],
            "document:entire_body",
            semantic,
        )
    return catalog


def _source_refs(
    catalog: dict[str, dict[str, Any]],
    record_ids: Sequence[str],
    locators: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    locators = locators or {}
    refs = []
    for record_id in record_ids:
        source = catalog[record_id]
        payload = {
            "record_id": source["record_id"],
            "source_system": source["source"],
            "source_version_id": source["source_version"],
            "source_record_id": source["source_record_id"],
            "record_version_id": source["record_version_id"],
            "raw_content_hash": source["raw_content_hash"],
            "raw_object_uri": source["raw_path"],
            "locator": locators.get(
                record_id,
                {
                    "kind": "record",
                    "field_path": "$",
                    "hash_scope": "canonical_logical_record",
                },
            ),
        }
        payload["source_ref_id"] = "src:{}:{}".format(
            source["record_version_id"], _sha256(_json_bytes(payload))
        )
        refs.append(payload)
    return refs
