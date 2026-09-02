from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.client import Config
from ingestion.adapters.s3.evidence_bucket import EvidenceBucket
from ingestion.ports.ingestion_ledger import Receipt

REQUIRED_ENV = (
    "TEST_EVIDENCE_S3_ENDPOINT",
    "TEST_EVIDENCE_S3_BUCKET",
    "TEST_EVIDENCE_S3_ACCESS_KEY",
    "TEST_EVIDENCE_S3_SECRET_KEY",
)
REPO_ROOT = Path(__file__).resolve().parents[6]
EDITION_DIR = REPO_ROOT / "data" / "dataset" / "editions" / "en" / "data"


@pytest.fixture
def object_client() -> Iterator[tuple[Any, str]]:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.fail(f"integration profile requires: {', '.join(missing)}")
    bucket = os.environ["TEST_EVIDENCE_S3_BUCKET"]
    if "test" not in bucket:
        pytest.fail("TEST_EVIDENCE_S3_BUCKET must name a disposable bucket containing 'test'")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["TEST_EVIDENCE_S3_ENDPOINT"],
        aws_access_key_id=os.environ["TEST_EVIDENCE_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["TEST_EVIDENCE_S3_SECRET_KEY"],
        region_name=os.environ.get("TEST_EVIDENCE_S3_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client.head_bucket(Bucket=bucket)
    try:
        yield client, bucket
    finally:
        page = client.list_objects_v2(Bucket=bucket)
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})


def test_seed_layout_materializes_and_receipt_round_trips(
    object_client: tuple[Any, str],
) -> None:
    client, bucket_name = object_client
    client.put_object(
        Bucket=bucket_name,
        Key="datasets/en/manifest.json",
        Body=(EDITION_DIR / "manifest.json").read_bytes(),
    )
    expected: set[Path] = set()
    for source in (EDITION_DIR / "raw").rglob("*"):
        if source.is_file():
            relative = source.relative_to(EDITION_DIR / "raw")
            expected.add(relative)
            client.put_object(
                Bucket=bucket_name,
                Key=f"datasets/en/raw/{relative.as_posix()}",
                Body=source.read_bytes(),
            )

    bucket = EvidenceBucket(client, bucket_name)
    with bucket.materialize_edition("en") as materialized:
        actual = {
            path.relative_to(materialized / "raw")
            for path in (materialized / "raw").rglob("*")
            if path.is_file()
        }
        assert actual == expected
        assert (materialized / "manifest.json").read_bytes() == (
            EDITION_DIR / "manifest.json"
        ).read_bytes()

    receipt = Receipt(
        fingerprint="a" * 64,
        dataset_version="trg-synth-en-v1.0.0",
        embedding_model_id="embed-test",
        chunking={"window_chars": 4000, "overlap_chars": 200},
        pipeline_version="ingestion@1",
        completed_at=datetime(2026, 9, 2, tzinfo=UTC),
        counts={"records": 142},
    )
    bucket.write("en", receipt)
    assert bucket.read("en") == receipt
