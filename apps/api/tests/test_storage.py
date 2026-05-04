"""Tests for the storage abstraction.

LocalStorage is exercised against a tmp dir; GCSStorage path is checked
via a stubbed bucket so no GCS client is constructed.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class LocalStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from storage import LocalStorage

        self.store = LocalStorage(root=Path(self.tmp.name))

    def test_put_get_roundtrip(self) -> None:
        self.store.put("hello.txt", b"world")
        self.assertEqual(self.store.get("hello.txt"), b"world")

    def test_put_creates_nested_directories(self) -> None:
        self.store.put("cases/abc/file.bin", b"data")
        self.assertTrue((Path(self.tmp.name) / "cases" / "abc" / "file.bin").exists())

    def test_delete_is_idempotent(self) -> None:
        self.store.put("k", b"v")
        self.store.delete("k")
        # Deleting twice must not raise.
        self.store.delete("k")

    def test_url_is_file_scheme(self) -> None:
        url = self.store.url("k.bin")
        self.assertTrue(url.startswith("file://"))
        self.assertIn("k.bin", url)

    def test_path_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put("../escape.txt", b"x")


class GetStorageTest(unittest.TestCase):
    """The factory should pick the right backend and refuse misconfiguration."""

    def setUp(self) -> None:
        # Reset memoised provider between tests.
        import storage as _storage

        _storage._storage = None
        self.addCleanup(lambda: setattr(_storage, "_storage", None))

    def test_default_is_local(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STORAGE_BACKEND", None)
            from storage import LocalStorage, get_storage

            self.assertIsInstance(get_storage(), LocalStorage)

    def test_explicit_local(self) -> None:
        with patch.dict(os.environ, {"STORAGE_BACKEND": "local"}):
            from storage import LocalStorage, get_storage

            self.assertIsInstance(get_storage(), LocalStorage)

    def test_unknown_backend_raises(self) -> None:
        with patch.dict(os.environ, {"STORAGE_BACKEND": "s3"}):
            from storage import get_storage

            with self.assertRaises(RuntimeError):
                get_storage()

    def test_gcs_requires_bucket_name(self) -> None:
        with patch.dict(os.environ, {"STORAGE_BACKEND": "gcs", "GCS_BUCKET": ""}):
            from storage import get_storage

            with self.assertRaises(RuntimeError):
                get_storage()


class GCSStorageTest(unittest.TestCase):
    """Confirm GCSStorage delegates correctly to the google-cloud-storage client."""

    def test_put_get_delete_url_with_stubbed_client(self) -> None:
        # Build a minimal stub of google.cloud.storage.Client / Bucket / Blob.
        captured: dict = {}

        class _Blob:
            def __init__(self, key: str) -> None:
                self.key = key

            def upload_from_string(self, data: bytes, content_type: str) -> None:
                captured["uploaded"] = (self.key, data, content_type)

            def download_as_bytes(self) -> bytes:
                return b"payload"

            def delete(self) -> None:
                captured["deleted"] = self.key

        class _Bucket:
            def blob(self, key: str) -> _Blob:
                return _Blob(key)

        class _Client:
            def bucket(self, name: str) -> _Bucket:
                captured["bucket_name"] = name
                return _Bucket()

        # Create a fake module path so the lazy `from google.cloud import storage as gcs` resolves.
        import types

        fake_mod = types.ModuleType("google.cloud.storage")
        fake_mod.Client = _Client  # type: ignore[attr-defined]
        with patch.dict("sys.modules", {"google.cloud.storage": fake_mod}):
            from storage import GCSStorage

            store = GCSStorage(bucket="medai-test")
            self.assertEqual(captured["bucket_name"], "medai-test")

            store.put("k", b"v", content_type="text/plain")
            self.assertEqual(captured["uploaded"], ("k", b"v", "text/plain"))

            self.assertEqual(store.get("k"), b"payload")

            store.delete("k")
            self.assertEqual(captured["deleted"], "k")

            self.assertEqual(store.url("k"), "gs://medai-test/k")


if __name__ == "__main__":
    unittest.main()
