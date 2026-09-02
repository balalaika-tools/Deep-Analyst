"""The ingestion receipt as one JSON file per edition under the local index directory."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ingestion.ports.ingestion_ledger import Receipt


class FileReceiptStore:
    def __init__(self, index_dir: Path) -> None:
        self._index_dir = index_dir

    def path_for(self, edition: str) -> Path:
        return self._index_dir / f"{edition}.json"

    def read(self, edition: str) -> Receipt | None:
        path = self.path_for(edition)
        if not path.is_file():
            return None
        try:
            return Receipt.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            # A malformed receipt is treated as absent: the run re-executes and rewrites it.
            return None

    def write(self, edition: str, receipt: Receipt) -> None:
        """Atomic replace so a crash mid-write never leaves a half receipt behind."""
        self._index_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        fd, temp_name = tempfile.mkstemp(dir=self._index_dir, prefix=f".{edition}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temp_name, self.path_for(edition))
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
