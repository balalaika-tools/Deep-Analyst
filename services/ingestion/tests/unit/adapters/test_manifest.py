from pathlib import Path

import pytest
from ingestion.adapters.fixtures.manifest import ManifestError, load_manifest


def test_manifest_exposes_case_version_and_totals(edition_dir: Path) -> None:
    manifest = load_manifest(edition_dir)
    assert manifest.case_id == "case_trg_001"
    assert manifest.dataset_version == "trg-synth-en-v1.0.0"
    assert manifest.edition == "en"
    assert manifest.source_totals["all_source_records"] == 142
    assert manifest.raw_bytes == (edition_dir / "manifest.json").read_bytes()


def test_missing_manifest_is_an_explicit_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        load_manifest(tmp_path)
