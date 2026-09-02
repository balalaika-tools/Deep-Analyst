"""Contract tests for the generated canonical TRG dataset.

Run with ``uv run pytest`` (or ``uv run python -m unittest discover``) from
the repository root so the ``dataset`` workspace member is on the path.
"""

import csv
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dataset import main as GENERATOR
from dataset.core.util import _global_record_id

DATASET_DIR = Path(__file__).resolve().parents[1]
DATA_ROOT = DATASET_DIR / "editions" / "en" / "data"
GREEK_DATA_ROOT = DATASET_DIR / "editions" / "el" / "data"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Return a relative-path-to-bytes map for an exact tree comparison."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sql_insert_ids(sql: str, table: str) -> list[str]:
    """Extract the stable first-column ID from one generated INSERT block."""
    pattern = re.compile(
        r"\bINSERT\s+INTO\s+" + re.escape(table) + r"\s*\([^;]*?\)\s*VALUES\s*(.*?);",
        re.IGNORECASE | re.DOTALL,
    )
    blocks = pattern.findall(sql)
    if len(blocks) != 1:
        raise AssertionError(f"expected one INSERT block for {table}, found {len(blocks)}")

    rows = [line.strip() for line in blocks[0].splitlines() if line.strip().startswith("(")]
    id_pattern = re.compile(r"^\('((?:[^']|'')*)'")
    ids = []
    for row in rows:
        match = id_pattern.match(row)
        if match is None:
            raise AssertionError(f"could not parse stable ID from {table} INSERT row: {row}")
        ids.append(match.group(1).replace("''", "'"))
    return ids


class DatasetContractTests(unittest.TestCase):
    maxDiff = None

    def _verified_manifest(self, root: Path = DATA_ROOT) -> dict[str, Any]:
        return GENERATOR.verify_manifest(root, expected_seed=GENERATOR.CANONICAL_SEED)

    def test_canonical_counts_and_stable_ids(self) -> None:
        manifest = self._verified_manifest()
        self.assertEqual(manifest["language"], "en")
        self.assertEqual(manifest["edition_role"], "primary")
        self.assertEqual(manifest["dataset_version"], "trg-synth-en-v1.0.0")

        expected_totals = {
            "cdr": 55,
            "extraction": 18,
            "emails": 6,
            "transactions": 35,
            "accounts": 18,
            "documents": 10,
            "all_source_records": 142,
            "core_story_records": 44,
            "background_records": 98,
        }
        self.assertEqual(manifest["source_totals"], expected_totals)

        with (DATA_ROOT / "raw/cdr.csv").open("r", encoding="utf-8", newline="") as stream:
            cdr_ids = {row["record_id"] for row in csv.DictReader(stream)}
        extraction_ids = {
            json.loads(line)["msg_id"]
            for line in (DATA_ROOT / "raw/extraction.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        }
        email_ids = {path.stem for path in (DATA_ROOT / "raw/emails").glob("*.eml")}
        document_ids = {path.stem for path in (DATA_ROOT / "raw/docs").glob("*.md")}

        sql = (DATA_ROOT / "raw/bank.sql").read_text(encoding="utf-8")
        account_ids = set(_sql_insert_ids(sql, "accounts"))
        transaction_ids = set(_sql_insert_ids(sql, "transactions"))

        expected_ids: dict[str, set[str]] = {
            "cdr": {f"c{index:02d}" for index in range(1, 56)},
            "extraction_message": {
                "X-204",
                "X-205",
                "X-206",
                "X-207",
                "X-208",
                "X-301",
                "X-302",
                "X-303",
            }
            | {f"X-N{index:02d}" for index in range(1, 11)},
            "email": {f"eM{index}" for index in range(1, 7)},
            "account": {
                "acct_pa",
                "acct_pr",
                "acct_pd",
                "acct_aegean",
                "acct_meridian",
                "acct_ionian",
            }
            | {f"nA{index:02d}" for index in range(1, 13)},
            "transaction": {
                "t_60",
                "t_85",
                "t_86",
                "t_88",
                "t_90",
                "t_B1",
                "t_B2",
                "t_B3",
            }
            | {f"nT{index:02d}" for index in range(1, 28)},
            "document": {
                "R-01",
                "R-02",
                "R-03",
                "R-04",
                "R-05",
                "R-06",
                "A-D1",
                "N-D1",
                "N-D2",
                "N-D3",
            },
        }
        raw_ids = {
            "cdr": cdr_ids,
            "extraction_message": extraction_ids,
            "email": email_ids,
            "account": account_ids,
            "transaction": transaction_ids,
            "document": document_ids,
        }
        self.assertEqual(raw_ids, expected_ids)

        manifest_ids: dict[str, set[str]] = {record_type: set() for record_type in expected_ids}
        for entry in manifest["raw_files"]:
            for record in entry["records"]:
                manifest_ids[record["record_type"]].add(record["source_record_id"])
        self.assertEqual(manifest_ids, expected_ids)
        self.assertEqual(
            sum(len(ids) for ids in raw_ids.values()),
            expected_totals["all_source_records"],
        )

    def test_source_local_identifier_collisions_remain_distinct(self) -> None:
        self.assertNotEqual(
            _global_record_id("cdr", "shared-1"),
            _global_record_id("email", "shared-1"),
        )
        self.assertEqual(_global_record_id("cdr", "shared-1"), "cdr:shared-1")

    def test_greek_alternate_edition_is_verified_and_semantically_equivalent(self) -> None:
        english_manifest = self._verified_manifest(DATA_ROOT)
        greek_manifest = self._verified_manifest(GREEK_DATA_ROOT)
        self.assertEqual(greek_manifest["language"], "el")
        self.assertEqual(greek_manifest["edition_role"], "alternate")
        self.assertEqual(greek_manifest["dataset_version"], "trg-synth-el-v1.0.0")
        self.assertEqual(english_manifest["source_totals"], greek_manifest["source_totals"])

        def record_inventory(manifest: dict[str, Any]) -> set[tuple[str, str]]:
            return {
                (record["record_type"], record["source_record_id"])
                for raw_file in manifest["raw_files"]
                for record in raw_file["records"]
            }

        self.assertEqual(record_inventory(english_manifest), record_inventory(greek_manifest))

        english_truth = json.loads((DATA_ROOT / "ground_truth.json").read_text(encoding="utf-8"))
        greek_truth = json.loads(
            (GREEK_DATA_ROOT / "ground_truth.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["id"] for item in english_truth["golden_questions"]},
            {item["id"] for item in greek_truth["golden_questions"]},
        )
        for layer in (english_truth, greek_truth):
            self.assertEqual(len(layer["golden_questions"]), 12)

        def assertion_semantics(truth: dict[str, Any]) -> set[tuple[str, str, str, str]]:
            assertions = truth["epistemic_layers"]["observable_entities_resolutions_assertions"][
                "relationship_assertions"
            ]
            return {
                (
                    item["subject_entity_id"],
                    item["predicate"],
                    item["object_entity_id"],
                    item["assertion_status"],
                )
                for item in assertions
            }

        self.assertEqual(assertion_semantics(english_truth), assertion_semantics(greek_truth))

    def test_primary_edition_contains_no_greek_source_text(self) -> None:
        greek_pattern = re.compile(r"[Α-Ωα-ωΆΈΉΊΌΎΏάέήίόύώΐΰϊϋ]")
        text_files = [
            path for path in DATA_ROOT.rglob("*") if path.is_file() and path.suffix != ".bin"
        ]
        offenders = [
            path.relative_to(DATA_ROOT).as_posix()
            for path in text_files
            if greek_pattern.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])

    def test_public_verifier_rejects_raw_file_hash_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trg-file-hash-test-") as tmp:
            copied = Path(tmp) / "data"
            shutil.copytree(DATA_ROOT, copied)
            with (copied / "raw/cdr.csv").open("ab") as stream:
                stream.write(b"\n# tampered\n")

            with self.assertRaises(ValueError):
                self._verified_manifest(copied)

    def test_public_verifier_rejects_record_hash_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trg-record-hash-test-") as tmp:
            copied = Path(tmp) / "data"
            shutil.copytree(DATA_ROOT, copied)
            manifest_path = copied / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["raw_files"][0]["records"][0]["record_sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    separators=(",", ": "),
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self._verified_manifest(copied)

    def test_two_independent_canonical_builds_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="trg-determinism-test-") as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"

            GENERATOR.generate_dataset(first, GENERATOR.CANONICAL_SEED)
            GENERATOR.generate_dataset(second, GENERATOR.CANONICAL_SEED)
            self._verified_manifest(first)
            self._verified_manifest(second)

            self.assertEqual(_tree_bytes(first), _tree_bytes(second))

    def test_quarantine_and_runtime_exclusions_are_outside_corpus(self) -> None:
        manifest = self._verified_manifest()
        self.assertEqual(
            manifest["runtime_exclusions"],
            ["ground_truth.json", "expected/", "fixtures/quarantine/"],
        )

        expected_outcomes = {
            "Q-CDR-01": "timestamp_parse_error",
            "Q-BANK-01": "iban_checksum_error",
            "Q-BANK-02": "currency_scale_error",
            "Q-DUP-01": "conflict_no_overwrite",
            "Q-EML-01": "email_header_error",
            "Q-DOC-01": "unsupported_format",
        }
        actual_outcomes = {
            fixture["fixture_id"]: fixture["expected_outcome"] for fixture in manifest["quarantine"]
        }
        self.assertEqual(actual_outcomes, expected_outcomes)
        self.assertTrue(
            all(fixture["included_in_corpus_totals"] is False for fixture in manifest["quarantine"])
        )

        manifest_fixture_paths = {fixture["path"] for fixture in manifest["quarantine"]}
        actual_fixture_paths = {
            path.relative_to(DATA_ROOT).as_posix()
            for path in (DATA_ROOT / "fixtures/quarantine").iterdir()
            if path.is_file()
        }
        self.assertEqual(actual_fixture_paths, manifest_fixture_paths)

        raw_paths = {entry["raw_path"] for entry in manifest["raw_files"]}
        self.assertNotIn("ground_truth.json", raw_paths)
        self.assertTrue(all(not path.startswith("expected/") for path in raw_paths))
        self.assertTrue(all(not path.startswith("fixtures/quarantine/") for path in raw_paths))
        self.assertEqual(
            sum(entry["record_count"] for entry in manifest["raw_files"]),
            142,
        )

    def test_bank_sql_is_postgresql_and_has_exact_model_counts(self) -> None:
        sql = (DATA_ROOT / "raw/bank.sql").read_text(encoding="utf-8")

        self.assertRegex(sql, r"(?im)^-- .*PostgreSQL 14\+")
        self.assertRegex(sql, r"(?i)\bBEGIN\s*;")
        self.assertRegex(sql, r"(?i)\bCOMMIT\s*;")
        self.assertRegex(sql, r"(?i)\bCREATE\s+TABLE\s+accounts\b")
        self.assertRegex(sql, r"(?i)\bCREATE\s+TABLE\s+transactions\b")
        self.assertRegex(
            sql,
            r"(?i)\bFOREIGN\s+KEY\s*\(debtor_iban\)",
        )
        self.assertRegex(
            sql,
            r"(?i)\bFOREIGN\s+KEY\s*\(creditor_iban\)",
        )
        for sqlite_syntax in (
            r"(?im)^\s*PRAGMA\b",
            r"(?i)\bAUTOINCREMENT\b",
            r"(?i)\bWITHOUT\s+ROWID\b",
            r"(?i)\bsqlite_[a-z0-9_]+\b",
            r"(?i)\blast_insert_rowid\s*\(",
        ):
            self.assertNotRegex(sql, sqlite_syntax)

        account_ids = _sql_insert_ids(sql, "accounts")
        transaction_ids = _sql_insert_ids(sql, "transactions")
        self.assertEqual(len(account_ids), 18)
        self.assertEqual(len(set(account_ids)), 18)
        self.assertEqual(len(transaction_ids), 35)
        self.assertEqual(len(set(transaction_ids)), 35)

    def test_generated_artifacts_contain_no_removed_scope_field(self) -> None:
        for root in (DATA_ROOT, GREEK_DATA_ROOT):
            offenders = [
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
                and path.suffix != ".bin"
                and re.search(
                    rf"{'_'.join(('case', 'id'))}|{'-'.join(('x', 'case', 'id'))}",
                    path.read_text(encoding="utf-8", errors="strict"),
                    re.IGNORECASE,
                )
            ]
            self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
