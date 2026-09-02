"""Generate a dataset release, replacing any existing output atomically."""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dataset.core.constants import CASE_ID, DEFAULT_LOCALE
from dataset.core.util import _require
from dataset.pipeline.models import _build_dataset_models
from dataset.pipeline.verify import verify_manifest
from dataset.pipeline.writer import write_dataset


def _existing_output_is_generated(output: Path) -> bool:
    if not output.exists():
        return True
    if not output.is_dir():
        return False
    if not any(output.iterdir()):
        return True
    try:
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        seed = manifest.get("rng_seed")
        if type(seed) is not int:
            return False
        # One-time migration path for the original Greek-only canonical build.
        if (
            "language" not in manifest
            and manifest.get("dataset_version") == "trg-synth-v1.0.0"
            and manifest.get("case_id") == CASE_ID
            and manifest.get("source_totals", {}).get("all_source_records") == 142
        ):
            return True
        verify_manifest(output, expected_seed=seed)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
    return True


def generate_dataset(output: Path, seed: int, locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    models = _build_dataset_models(seed, locale)

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _require(
        _existing_output_is_generated(output),
        f"refusing to replace a non-generated output directory: {output}",
    )
    temporary = Path(tempfile.mkdtemp(prefix=".trg-dataset-build-", dir=str(output.parent)))
    try:
        manifest = write_dataset(temporary, **models)
        verify_manifest(temporary, expected_seed=seed)
        if output.exists():
            backup = temporary.with_name(temporary.name + "-previous")
            _require(not backup.exists(), "temporary backup path already exists")
            output.rename(backup)
            try:
                temporary.rename(output)
            except BaseException:
                backup.rename(output)
                raise
            shutil.rmtree(backup)
        else:
            temporary.rename(output)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
