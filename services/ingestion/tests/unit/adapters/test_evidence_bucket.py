from __future__ import annotations

import io
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from ingestion.adapters.s3.evidence_bucket import EvidenceBoundaryError, EvidenceBucket
from ingestion.ports.ingestion_ledger import Receipt


class FakeObjectClient:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.listed_prefixes: list[str] = []
        self.downloaded: list[str] = []
        self.listing_override: list[str] | None = None

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        prefix = str(kwargs["Prefix"])
        self.listed_prefixes.append(prefix)
        keys = self.listing_override
        if keys is None:
            keys = [key for key in self.objects if key.startswith(prefix)]
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def download_fileobj(self, bucket: str, key: str, fileobj: Any) -> None:
        self.downloaded.append(key)
        fileobj.write(self.objects[key])

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[key])}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])
        return {}


def _receipt() -> Receipt:
    return Receipt(
        fingerprint="f" * 64,
        dataset_version="dataset-v1",
        embedding_model_id="embed-v1",
        chunking={"window_chars": 4000, "overlap_chars": 200},
        pipeline_version="ingestion@1",
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
        counts={"records": 1},
    )


def test_materialization_reads_only_the_edition_contract_and_is_private(
    edition_dir: Path,
) -> None:
    manifest = (edition_dir / "manifest.json").read_bytes()
    cdr = (edition_dir / "raw" / "cdr.csv").read_bytes()
    client = FakeObjectClient(
        {
            "datasets/en/manifest.json": manifest,
            "datasets/en/raw/cdr.csv": cdr,
            "datasets/el/raw/cdr.csv": b"must not be listed",
            "indexes/en/receipt.json": b"must not be listed",
        }
    )
    bucket = EvidenceBucket(client, "evidence")

    with bucket.materialize_edition("en") as root:
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert (root / "manifest.json").read_bytes() == manifest
        assert (root / "raw" / "cdr.csv").read_bytes() == cdr
        materialized_root = root

    assert client.listed_prefixes == ["datasets/en/"]
    assert client.downloaded == ["datasets/en/raw/cdr.csv"]
    assert not materialized_root.exists()


@pytest.mark.parametrize(
    "key",
    [
        "ground_truth.json",
        "datasets/en/ground_truth.json",
        "datasets/en/raw/../ground_truth.json",
    ],
)
def test_materialization_refuses_keys_outside_the_allowed_layout(
    edition_dir: Path, key: str
) -> None:
    client = FakeObjectClient(
        {
            "datasets/en/manifest.json": (edition_dir / "manifest.json").read_bytes(),
            "datasets/en/raw/cdr.csv": b"cdr",
        }
    )
    client.listing_override = [key]

    with pytest.raises(EvidenceBoundaryError, match="refusing"):
        with EvidenceBucket(client, "evidence").materialize_edition("en"):
            pass
    assert client.downloaded == []


def test_receipt_is_absent_then_round_trips_at_the_edition_index_key() -> None:
    client = FakeObjectClient({})
    bucket = EvidenceBucket(client, "evidence")

    assert bucket.read("en") is None
    bucket.write("en", _receipt())

    assert bucket.read("en") == _receipt()
    assert set(client.objects) == {"indexes/en/receipt.json"}
