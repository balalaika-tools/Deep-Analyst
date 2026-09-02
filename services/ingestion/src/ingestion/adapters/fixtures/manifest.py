"""The edition manifest: the only file outside raw/ that ingestion may read."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """The edition directory does not hold a readable manifest."""


@dataclass(frozen=True, slots=True)
class Manifest:
    path: Path
    raw_bytes: bytes
    dataset_version: str
    language: str
    source_totals: dict[str, int]

    @property
    def edition(self) -> str:
        return self.language


def parse_manifest(raw: bytes, *, path: Path) -> Manifest:
    """Validate manifest bytes obtained from any evidence transport."""
    data: dict[str, Any] = json.loads(raw)
    try:
        return Manifest(
            path=path,
            raw_bytes=raw,
            dataset_version=str(data["dataset_version"]),
            language=str(data["language"]),
            source_totals={k: int(v) for k, v in data["source_totals"].items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestError(f"manifest.json is missing a required field: {exc}") from exc


def load_manifest(edition_dir: Path) -> Manifest:
    path = edition_dir / "manifest.json"
    if not path.is_file():
        raise ManifestError(f"manifest.json not found in {edition_dir}")
    raw = path.read_bytes()
    return parse_manifest(raw, path=path)
