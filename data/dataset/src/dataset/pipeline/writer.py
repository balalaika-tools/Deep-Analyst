"""Write one dataset build to disk and assemble its manifest."""

import csv
import io
from pathlib import Path
from typing import Any

from dataset.core import state
from dataset.core.constants import (
    ACCOUNT_COLUMNS,
    CDR_COLUMNS,
    DEFAULT_LOCALE,
    GENERATED_AT,
    POLICY_VERSION,
    SYNTHETIC_NOTICE,
    TRANSACTION_COLUMNS,
)
from dataset.core.util import (
    _cdr_lexemes,
    _json_bytes,
    _ordered_row_hash,
    _record_hash,
    _sha256,
    _write_bytes,
    _write_json,
    _write_text,
)
from dataset.sql import render_bank_sql


def _raw_file_entry(
    root: Path,
    raw_path: str,
    source: str,
    source_version: str,
    records: list[dict[str, str]],
) -> dict[str, Any]:
    data = (root / raw_path).read_bytes()
    return {
        "source": source,
        "source_version": source_version,
        "raw_path": raw_path,
        "file_sha256": _sha256(data),
        "record_count": len(records),
        "records": records,
    }


def write_dataset(
    root: Path,
    seed: int,
    variant_id: str | None,
    cdr: list[dict[str, Any]],
    extraction: list[dict[str, Any]],
    emails: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    policy: dict[str, Any],
    ground_truth: dict[str, Any],
    quarantine_files: dict[str, bytes],
    quarantine_expected: list[dict[str, str]],
    previews: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    _write_text(root / "SYNTHETIC_DATA_NOTICE.txt", SYNTHETIC_NOTICE)

    cdr_stream = io.StringIO(newline="")
    cdr_writer = csv.DictWriter(cdr_stream, fieldnames=CDR_COLUMNS, lineterminator="\n")
    cdr_writer.writeheader()
    cdr_writer.writerows(cdr)
    _write_text(root / "raw/cdr.csv", cdr_stream.getvalue())

    extraction_bytes = b"".join(_json_bytes(row) for row in extraction)
    _write_bytes(root / "raw/extraction.jsonl", extraction_bytes)
    _write_text(root / "raw/bank.sql", render_bank_sql(accounts, transactions))

    for email in emails:
        _write_bytes(root / "raw/emails/{}.eml".format(email["email_id"]), email["raw_bytes"])
    for document in documents:
        _write_bytes(root / "raw/docs/{}.md".format(document["document_id"]), document["raw_bytes"])

    _write_json(root / f"policies/{POLICY_VERSION}.json", policy)
    _write_json(root / "ground_truth.json", ground_truth)
    for filename, data in quarantine_files.items():
        _write_bytes(root / "fixtures/quarantine" / filename, data)
    for name, preview in previews.items():
        _write_json(root / f"expected/previews/{name}.json", preview)

    raw_files: list[dict[str, Any]] = []
    raw_files.append(
        _raw_file_entry(
            root,
            "raw/cdr.csv",
            "cdr",
            state.SOURCE_VERSIONS["cdr"],
            [
                {
                    "source_record_id": row["record_id"],
                    "record_type": "cdr",
                    "record_sha256": _record_hash(_cdr_lexemes(row)),
                }
                for row in sorted(cdr, key=lambda item: item["record_id"])
            ],
        )
    )
    raw_files.append(
        _raw_file_entry(
            root,
            "raw/extraction.jsonl",
            "extraction",
            state.SOURCE_VERSIONS["extraction"],
            [
                {
                    "source_record_id": row["msg_id"],
                    "record_type": "extraction_message",
                    "record_sha256": _record_hash(row),
                }
                for row in sorted(extraction, key=lambda item: item["msg_id"])
            ],
        )
    )
    bank_records = [
        {
            "source_record_id": row["account_id"],
            "record_type": "account",
            "record_sha256": _ordered_row_hash(row, ACCOUNT_COLUMNS),
        }
        for row in accounts
    ] + [
        {
            "source_record_id": row["txn_id"],
            "record_type": "transaction",
            "record_sha256": _ordered_row_hash(row, TRANSACTION_COLUMNS),
        }
        for row in transactions
    ]
    raw_files.append(
        _raw_file_entry(
            root,
            "raw/bank.sql",
            "bank",
            state.SOURCE_VERSIONS["bank"],
            bank_records,
        )
    )
    for email in emails:
        email_record = {
            "email_id": email["email_id"],
            "headers": email["headers"],
            "body": email["body"],
        }
        raw_files.append(
            _raw_file_entry(
                root,
                "raw/emails/{}.eml".format(email["email_id"]),
                "email",
                state.SOURCE_VERSIONS["email"],
                [
                    {
                        "source_record_id": email["email_id"],
                        "record_type": "email",
                        "record_sha256": _record_hash(email_record),
                    }
                ],
            )
        )
    for document in documents:
        document_record = {
            "document_id": document["document_id"],
            "front_matter": document["front_matter"],
            "body": document["body"],
        }
        raw_files.append(
            _raw_file_entry(
                root,
                "raw/docs/{}.md".format(document["document_id"]),
                "docs",
                state.SOURCE_VERSIONS["docs"],
                [
                    {
                        "source_record_id": document["document_id"],
                        "record_type": "document",
                        "record_sha256": _record_hash(document_record),
                    }
                ],
            )
        )
    raw_files.sort(key=lambda item: item["raw_path"])

    quarantine_manifest = []
    for expected in sorted(quarantine_expected, key=lambda item: item["fixture_id"]):
        relative_path = "fixtures/quarantine/{}".format(expected["file"])
        quarantine_manifest.append(
            {
                **expected,
                "path": relative_path,
                "file_sha256": _sha256((root / relative_path).read_bytes()),
                "included_in_corpus_totals": False,
            }
        )

    artifact_paths = [
        "SYNTHETIC_DATA_NOTICE.txt",
        "ground_truth.json",
        f"policies/{POLICY_VERSION}.json",
    ] + [f"expected/previews/{name}.json" for name in sorted(previews)]
    non_corpus_artifacts = [
        {"path": path, "file_sha256": _sha256((root / path).read_bytes())}
        for path in artifact_paths
    ]

    manifest = {
        "manifest_schema_version": "trg-manifest@1",
        "dataset_version": state.DATASET_VERSION,
        "language": state.ACTIVE_LOCALE,
        "edition_role": "primary" if state.ACTIVE_LOCALE == DEFAULT_LOCALE else "alternate",
        "policy_version": POLICY_VERSION,
        "variant_id": variant_id,
        "rng_seed": seed,
        "generated_at": GENERATED_AT,
        "synthetic_data": True,
        "synthetic_data_notice": SYNTHETIC_NOTICE.strip(),
        "activity_window": {"from": "2026-02-20", "through": "2026-03-10"},
        "raw_local_timezone": "Europe/Athens (UTC+02:00 for this fixture)",
        "canonical_timezone": "UTC",
        "source_versions": state.SOURCE_VERSIONS,
        "source_totals": {
            "cdr": 55,
            "extraction": 18,
            "emails": 6,
            "transactions": 35,
            "accounts": 18,
            "documents": 10,
            "all_source_records": 142,
            "core_story_records": 44,
            "background_records": 98,
        },
        "database": {
            "dialect": "PostgreSQL 14+",
            "raw_path": "raw/bank.sql",
            "tables": {"accounts": 18, "transactions": 35},
            "packaging_override": "User-requested PostgreSQL SQL replaces DATASET_SPEC.md's bank.sqlite artifact; logical columns, rows and constraints are preserved.",
        },
        "hash_contract": {
            "algorithm": "SHA-256",
            "file_hashes": "exact bytes",
            "record_hashes": {
                "cdr": "canonical UTF-8 JSON object in CDR_COLUMNS; every value is the exact CSV string lexeme; sorted keys, compact separators and one LF",
                "extraction": "canonical UTF-8 JSON object using the JSONL value types; sorted keys, compact separators and one LF",
                "email": "canonical UTF-8 JSON object containing email_id, parsed headers and body; sorted keys, compact separators and one LF",
                "document": "canonical UTF-8 JSON object containing document_id, parsed front matter and body; sorted keys, compact separators and one LF",
            },
            "bank_record_hashes": {
                "serialization": "canonical UTF-8 JSON array in declared schema-column order with one LF",
                "account_columns": ACCOUNT_COLUMNS,
                "transaction_columns": TRANSACTION_COLUMNS,
            },
        },
        "raw_files": raw_files,
        "quarantine": quarantine_manifest,
        "non_corpus_artifacts": non_corpus_artifacts,
        "runtime_exclusions": ["ground_truth.json", "expected/", "fixtures/quarantine/"],
        "toolchain": {
            "reference_python": "3.9.6",
            "python_compatibility": ">=3.9",
            "generator": "dataset/src/dataset/main.py",
            "sql_dialect": "PostgreSQL 14+",
            "text_encoding": "UTF-8",
            "line_endings": "LF",
        },
    }
    _write_json(root / "manifest.json", manifest)
    return manifest
