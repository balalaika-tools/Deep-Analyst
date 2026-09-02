"""Bucket-scoped evidence materialization and ingestion receipt persistence."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncEngine

from ingestion.adapters.fixtures.edition import SOURCE_ORDER, EditionSources
from ingestion.ports.ingestion_ledger import Receipt

if TYPE_CHECKING:
    from ingestion.config.settings import Settings
    from ingestion.domain.records import SourceBatch


class EvidenceBoundaryError(ValueError):
    """The bucket exposed an object outside the permitted edition layout."""


class ObjectClient(Protocol):
    """The small boto3 S3 surface owned by this adapter."""

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...

    def download_fileobj(self, bucket: str, key: str, fileobj: Any) -> None: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


def _safe_edition(edition: str) -> str:
    if not edition or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in edition):
        raise EvidenceBoundaryError(f"invalid dataset edition: {edition!r}")
    return edition


class EvidenceBucket:
    """Read one edition prefix and store its completion receipt."""

    def __init__(self, client: ObjectClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket
        self._manifest_cache: dict[str, bytes] = {}

    def read_manifest_bytes(self, edition: str) -> bytes:
        edition = _safe_edition(edition)
        cached = self._manifest_cache.get(edition)
        if cached is not None:
            return cached
        response = self._client.get_object(
            Bucket=self._bucket, Key=f"datasets/{edition}/manifest.json"
        )
        body = response["Body"]
        try:
            payload = body.read()
        finally:
            body.close()
        if not isinstance(payload, bytes):
            raise TypeError("S3 manifest body did not return bytes")
        self._manifest_cache[edition] = payload
        return payload

    @contextmanager
    def materialize_edition(self, edition: str) -> Iterator[Path]:
        """Download only the allowed edition objects into a mode-0700 temporary root."""
        edition = _safe_edition(edition)
        with tempfile.TemporaryDirectory(prefix=f"ingestion-{edition}-") as temp_name:
            root = Path(temp_name)
            (root / "raw").mkdir()
            (root / "manifest.json").write_bytes(self.read_manifest_bytes(edition))
            raw_count = self._download_raw(edition, root)
            if raw_count == 0:
                raise EvidenceBoundaryError(f"datasets/{edition}/raw/ contains no objects")
            yield root

    def _download_raw(self, edition: str, root: Path) -> int:
        prefix = f"datasets/{edition}/"
        continuation: str | None = None
        downloaded = 0
        while True:
            request: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if continuation:
                request["ContinuationToken"] = continuation
            page = self._client.list_objects_v2(**request)
            for item in page.get("Contents", []):
                key = str(item["Key"])
                target = self._target_for_key(edition, key, root)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    self._client.download_fileobj(self._bucket, key, handle)
                downloaded += 1
            if not page.get("IsTruncated"):
                return downloaded
            continuation = str(page.get("NextContinuationToken") or "")
            if not continuation:
                raise EvidenceBoundaryError("truncated S3 listing omitted a continuation token")

    @staticmethod
    def _target_for_key(edition: str, key: str, root: Path) -> Path | None:
        manifest_key = f"datasets/{edition}/manifest.json"
        raw_prefix = f"datasets/{edition}/raw/"
        if key == manifest_key or (key.startswith(raw_prefix) and key.endswith("/")):
            return None
        if not key.startswith(raw_prefix):
            raise EvidenceBoundaryError(f"refusing object outside allowed edition layout: {key}")
        relative = PurePosixPath(key.removeprefix(raw_prefix))
        if not relative.parts or ".." in relative.parts or "\\" in str(relative):
            raise EvidenceBoundaryError(f"refusing unsafe evidence object key: {key}")
        return root / "raw" / Path(*relative.parts)

    def read(self, edition: str) -> Receipt | None:
        key = self.receipt_key(edition)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        body = response["Body"]
        try:
            payload = body.read()
        finally:
            body.close()
        try:
            return Receipt.model_validate_json(payload)
        except ValueError:
            return None

    def write(self, edition: str, receipt: Receipt) -> None:
        payload = json.dumps(receipt.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=self.receipt_key(edition),
            Body=payload,
            ContentType="application/json",
        )

    @staticmethod
    def receipt_key(edition: str) -> str:
        return f"indexes/{_safe_edition(edition)}/receipt.json"


class S3EditionSources:
    """Lazy file adapters: raw evidence is materialized only after the skip check."""

    def __init__(
        self, bucket: EvidenceBucket, edition: str, case_id: str, engine: AsyncEngine
    ) -> None:
        self._bucket = bucket
        self._edition = edition
        self._case_id = case_id
        self._engine = engine
        self._materialization: Any | None = None
        self._sources: EditionSources | None = None

    @property
    def source_systems(self) -> Sequence[str]:
        return SOURCE_ORDER

    async def load(self, source_system: str) -> SourceBatch:
        if self._sources is None:
            materialization = self._bucket.materialize_edition(self._edition)
            edition_dir = materialization.__enter__()
            self._materialization = materialization
            self._sources = EditionSources(edition_dir, self._case_id, self._engine)
        return await self._sources.load(source_system)

    def close(self) -> None:
        if self._materialization is not None:
            self._materialization.__exit__(None, None, None)
            self._materialization = None
            self._sources = None


def build_evidence_bucket(settings: Settings) -> EvidenceBucket:
    """Build a path-style S3 client from the validated deployment contract."""
    client = boto3.client(
        "s3",
        endpoint_url=str(settings.evidence_s3_endpoint).rstrip("/"),
        aws_access_key_id=settings.evidence_s3_access_key.get_secret_value(),
        aws_secret_access_key=settings.evidence_s3_secret_key.get_secret_value(),
        region_name=settings.aws_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return EvidenceBucket(client, settings.evidence_s3_bucket)
