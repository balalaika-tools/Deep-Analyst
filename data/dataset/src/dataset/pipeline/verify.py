"""Verify an existing dataset release against a clean deterministic rebuild."""

import json
import tempfile
from pathlib import Path
from typing import Any

from dataset.core import state
from dataset.core.constants import SUPPORTED_LOCALES
from dataset.core.state import _activate_locale
from dataset.core.util import _require
from dataset.pipeline.models import _build_dataset_models
from dataset.pipeline.writer import write_dataset


def _relative_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_manifest(root: Path, expected_seed: int | None = None) -> dict[str, Any]:
    """Verify the complete release against a clean deterministic rebuild.

    A manifest cannot validate itself: trusting only its declared hashes would
    allow a coordinated edit of content and hash metadata.  Rebuilding from the
    pinned seed independently validates every raw byte, per-record hash,
    artifact, quarantine fixture and inventory entry.
    """
    manifest_path = root / "manifest.json"
    _require(manifest_path.is_file(), f"manifest.json not found in {root}")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict), "manifest root must be a JSON object")
    locale = manifest.get("language")
    _require(
        isinstance(locale, str) and locale in SUPPORTED_LOCALES, "manifest language is unsupported"
    )
    assert isinstance(locale, str)
    _activate_locale(locale)
    _require(manifest.get("dataset_version") == state.DATASET_VERSION, "dataset version mismatch")
    _require(manifest.get("synthetic_data") is True, "synthetic dataset marker missing")
    seed = manifest.get("rng_seed")
    _require(type(seed) is int, "manifest rng_seed must be an integer")
    assert isinstance(seed, int)
    if expected_seed is not None:
        _require(seed == expected_seed, "manifest seed mismatch")

    symlinks = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink()]
    _require(
        not symlinks, "dataset must not contain symbolic links: {}".format(", ".join(symlinks))
    )

    models = _build_dataset_models(seed, locale)
    with tempfile.TemporaryDirectory(prefix="trg-dataset-verify-") as temp_dir:
        expected_root = Path(temp_dir) / "data"
        write_dataset(expected_root, **models)
        actual_files = _relative_files(root)
        expected_files = _relative_files(expected_root)
        _require(
            set(actual_files) == set(expected_files),
            "dataset file inventory differs from deterministic build",
        )
        for relative_path in sorted(expected_files):
            actual_bytes = actual_files[relative_path].read_bytes()
            expected_bytes = expected_files[relative_path].read_bytes()
            _require(
                actual_bytes == expected_bytes,
                f"dataset content differs from deterministic build: {relative_path}",
            )
    return manifest
