"""Blob/file storage abstraction.

Switched by the `STORAGE_BACKEND` env var:

- `local` (default) — writes under `apps/api/storage_data/`; good for dev.
- `gcs`              — writes to the bucket named by `GCS_BUCKET`; good for Cloud Run.

All backends expose the same four operations: ``put``, ``get``, ``delete``,
``url``. Callers should only depend on this module, never on filesystem paths
or google-cloud-storage types directly — that keeps the migration to GCS a
single-file swap.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def url(self, key: str) -> str: ...


class LocalStorage:
    """Filesystem-backed storage for local development."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parent / "storage_data"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Guard against path traversal; keys are app-generated but be defensive.
        if ".." in Path(key).parts:
            raise ValueError(f"invalid storage key: {key!r}")
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self._path(key).write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def url(self, key: str) -> str:
        # No direct URL for local; callers should proxy through the API.
        return f"file://{self._path(key)}"


class GCSStorage:
    """Google Cloud Storage backend for deployed environments."""

    def __init__(self, bucket: str) -> None:
        # Imported lazily so the dependency cost is only paid when GCS is selected.
        from google.cloud import storage as gcs

        self._client = gcs.Client()
        self._bucket = self._client.bucket(bucket)
        self._bucket_name = bucket

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        blob = self._bucket.blob(key)
        blob.upload_from_string(data, content_type=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self._bucket.blob(key).download_as_bytes()

    def delete(self, key: str) -> None:
        self._bucket.blob(key).delete()

    def url(self, key: str) -> str:
        return f"gs://{self._bucket_name}/{key}"


_storage: Storage | None = None


def get_storage() -> Storage:
    """Return the configured storage backend (memoised)."""
    global _storage
    if _storage is not None:
        return _storage
    backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if backend == "gcs":
        bucket = os.environ.get("GCS_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError("STORAGE_BACKEND=gcs requires GCS_BUCKET to be set.")
        _storage = GCSStorage(bucket)
    elif backend == "local":
        _storage = LocalStorage()
    else:
        raise RuntimeError(f"Unknown STORAGE_BACKEND={backend!r}. Use 'local' or 'gcs'.")
    return _storage


__all__ = ["Storage", "LocalStorage", "GCSStorage", "get_storage"]
