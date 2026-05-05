"""Tests for the case-attachments module.

PostgresAttachmentStore is exercised against a stubbed psycopg-style
connection. extract_text and format_attachment_block are tested as
pure functions.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch


# ── stub connection ────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, row=None, rows=None, rowcount: int = 0) -> None:
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeCon:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0
        # Programmable responses keyed by SQL substring.
        self._count_response: dict[str, Any] | None = {"n": 0}
        self._list_rows: list[dict[str, Any]] = []
        self._delete_rowcount: int = 0

    def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        self.executed.append((sql, params))
        if "COUNT(*)" in sql:
            return _FakeCursor(row=self._count_response)
        if sql.strip().upper().startswith("SELECT") and "FROM case_attachments" in sql:
            return _FakeCursor(rows=self._list_rows)
        if sql.strip().upper().startswith("DELETE"):
            return _FakeCursor(rowcount=self._delete_rowcount)
        return _FakeCursor()

    def commit(self) -> None:
        self.commits += 1


# ── extract_text + is_mime_supported ────────────────────────────────────────


class ExtractTextTest(unittest.TestCase):
    def test_empty_blob_returns_empty(self) -> None:
        from medai_api.attachments import extract_text

        self.assertEqual(extract_text(b"", "text/plain", "x.txt"), "")

    def test_text_blob_decoded(self) -> None:
        from medai_api.attachments import extract_text

        self.assertEqual(extract_text(b"  hi there\n", "text/plain", "x.txt"), "hi there")

    def test_text_subtypes_are_decoded(self) -> None:
        from medai_api.attachments import extract_text

        # text/* prefix branch
        self.assertEqual(extract_text(b"hello", "text/x-custom", "f.txt"), "hello")

    def test_image_returns_placeholder(self) -> None:
        from medai_api.attachments import extract_text

        out = extract_text(b"\x89PNG\r\n", "image/png", "scan.png")
        self.assertIn("[Image attached:", out)
        self.assertIn("scan.png", out)

    def test_unknown_mime_returns_placeholder(self) -> None:
        from medai_api.attachments import extract_text

        out = extract_text(b"...", "application/x-unknown", "f.bin")
        self.assertIn("[File attached:", out)


class IsMimeSupportedTest(unittest.TestCase):
    def test_text_pdf_image_supported(self) -> None:
        from medai_api.attachments import is_mime_supported

        for mime in ("text/plain", "text/markdown", "application/pdf", "image/png", "image/jpeg"):
            self.assertTrue(is_mime_supported(mime), msg=mime)

    def test_arbitrary_supported_via_prefix(self) -> None:
        from medai_api.attachments import is_mime_supported

        self.assertTrue(is_mime_supported("text/x-custom"))
        self.assertTrue(is_mime_supported("image/x-rare"))

    def test_unsupported(self) -> None:
        from medai_api.attachments import is_mime_supported

        self.assertFalse(is_mime_supported("application/octet-stream"))
        self.assertFalse(is_mime_supported("video/mp4"))


# ── PostgresAttachmentStore ─────────────────────────────────────────────────


class PostgresAttachmentStoreSaveTest(unittest.TestCase):
    def setUp(self) -> None:
        from medai_api.attachments import PostgresAttachmentStore

        self.store = PostgresAttachmentStore()

    def test_save_inserts_with_size_and_id(self) -> None:
        con = _FakeCon()
        row = self.store.save(
            con,
            case_id="case_1",
            user_id="user_1",
            user_plan="free",
            kind="file",
            filename="report.txt",
            mime_type="text/plain",
            blob=b"hello world",
            text="hello world",
            question_index=0,
        )
        self.assertEqual(row.case_id, "case_1")
        self.assertEqual(row.kind, "file")
        self.assertEqual(row.size_bytes, len(b"hello world"))
        self.assertTrue(row.id.startswith("att_"))
        self.assertEqual(con.commits, 1)
        # Two executes: COUNT then INSERT
        self.assertEqual(len(con.executed), 2)
        self.assertIn("INSERT INTO case_attachments", con.executed[1][0])

    def test_save_rejects_oversize_for_free_tier(self) -> None:
        from medai_api.attachments import AttachmentStoreError

        con = _FakeCon()
        oversized = b"x" * (1 * 1024 * 1024 + 1)  # 1 MB + 1 byte
        with self.assertRaises(AttachmentStoreError) as cm:
            self.store.save(
                con,
                case_id="c",
                user_id="u",
                user_plan="free",
                kind="file",
                filename="big.bin",
                mime_type="application/octet-stream",
                blob=oversized,
                text="",
                question_index=None,
            )
        self.assertEqual(cm.exception.code, "attachment_size")

    def test_save_rejects_count_cap_for_free_tier(self) -> None:
        from medai_api.attachments import AttachmentStoreError, FREE_PER_CASE_LIMIT

        con = _FakeCon()
        con._count_response = {"n": FREE_PER_CASE_LIMIT}
        with self.assertRaises(AttachmentStoreError) as cm:
            self.store.save(
                con,
                case_id="c",
                user_id="u",
                user_plan="free",
                kind="pasted",
                filename=None,
                mime_type="text/plain",
                blob=None,
                text="some pasted content",
                question_index=None,
            )
        self.assertEqual(cm.exception.code, "attachment_cap")

    def test_pro_tier_allows_more_and_larger(self) -> None:
        con = _FakeCon()
        # 2 MB blob — over free limit, under pro limit
        blob = b"x" * (2 * 1024 * 1024)
        row = self.store.save(
            con,
            case_id="c",
            user_id="u",
            user_plan="pro",
            kind="file",
            filename="file.bin",
            mime_type="application/octet-stream",
            blob=blob,
            text="",
            question_index=None,
        )
        self.assertEqual(row.size_bytes, len(blob))


class PostgresAttachmentStoreOtherOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        from medai_api.attachments import PostgresAttachmentStore

        self.store = PostgresAttachmentStore()

    def test_ensure_schema_creates_table_and_index(self) -> None:
        con = _FakeCon()
        self.store.ensure_schema(con)
        sqls = [s for s, _ in con.executed]
        self.assertTrue(any("CREATE TABLE" in s for s in sqls))
        self.assertTrue(any("CREATE INDEX" in s for s in sqls))
        self.assertEqual(con.commits, 1)

    def test_list_for_case_maps_rows_to_dataclasses(self) -> None:
        from datetime import datetime, timezone

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        con = _FakeCon()
        con._list_rows = [
            {
                "id": "att_1",
                "case_id": "c1",
                "user_id": "u1",
                "kind": "file",
                "filename": "lab.pdf",
                "mime_type": "application/pdf",
                "text": "result",
                "size_bytes": 9,
                "question_index": 1,
                "created_at": now,
            }
        ]
        rows = self.store.list_for_case(con, "c1")
        self.assertEqual(rows[0].id, "att_1")
        self.assertEqual(rows[0].size_bytes, 9)
        # datetime → ISO string
        self.assertTrue(rows[0].created_at.startswith("2026-01-01"))

    def test_get_texts_for_case_aliases_list_for_case(self) -> None:
        con = _FakeCon()
        con._list_rows = []
        # Same shape; just an aliased call.
        self.assertEqual(self.store.get_texts_for_case(con, "c"), [])

    def test_delete_returns_true_when_row_removed(self) -> None:
        con = _FakeCon()
        con._delete_rowcount = 1
        self.assertTrue(self.store.delete(con, "att_1", "user_1"))
        self.assertEqual(con.commits, 1)

    def test_delete_returns_false_when_no_row(self) -> None:
        con = _FakeCon()
        con._delete_rowcount = 0
        self.assertFalse(self.store.delete(con, "att_missing", "user_1"))


# ── factory + Gcs stub ──────────────────────────────────────────────────────


class GetAttachmentStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        from medai_api import attachments as _att

        _att._store = None
        self.addCleanup(lambda: setattr(_att, "_store", None))

    def test_default_is_postgres(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ATTACHMENT_STORE", None)
            from medai_api.attachments import PostgresAttachmentStore, get_attachment_store

            self.assertIsInstance(get_attachment_store(), PostgresAttachmentStore)

    def test_explicit_gcs_returns_stub(self) -> None:
        with patch.dict(os.environ, {"ATTACHMENT_STORE": "gcs"}):
            from medai_api.attachments import GcsAttachmentStore, get_attachment_store

            store = get_attachment_store()
            self.assertIsInstance(store, GcsAttachmentStore)
            with self.assertRaises(NotImplementedError):
                store.ensure_schema(None)


# ── format_attachment_block ─────────────────────────────────────────────────


class FormatAttachmentBlockTest(unittest.TestCase):
    def _row(self, **overrides):
        from medai_api.attachments import AttachmentRow

        defaults = dict(
            id="x",
            case_id="c",
            user_id="u",
            kind="file",
            filename="lab.pdf",
            mime_type="application/pdf",
            text="result text",
            size_bytes=11,
            question_index=None,
            created_at="2026-01-01",
        )
        defaults.update(overrides)
        return AttachmentRow(**defaults)

    def test_empty_returns_empty_string(self) -> None:
        from medai_api.attachments import format_attachment_block

        self.assertEqual(format_attachment_block([]), "")

    def test_renders_file_attachment(self) -> None:
        from medai_api.attachments import format_attachment_block

        out = format_attachment_block([self._row()])
        self.assertIn("--- Test results provided by patient ---", out)
        self.assertIn("file: lab.pdf", out)
        self.assertIn("result text", out)
        self.assertTrue(out.endswith("---"))

    def test_renders_pasted_text(self) -> None:
        from medai_api.attachments import format_attachment_block

        out = format_attachment_block([self._row(kind="pasted", filename=None, text="ECG: NSR")])
        self.assertIn("pasted text", out)
        self.assertIn("ECG: NSR", out)

    def test_question_index_links_to_question(self) -> None:
        from medai_api.attachments import format_attachment_block

        out = format_attachment_block(
            [self._row(question_index=1)],
            question_texts=["Onset?", "Severity?"],
        )
        self.assertIn("related to Q2", out)
        self.assertIn("Severity?", out)

    def test_no_text_falls_back_to_placeholder(self) -> None:
        from medai_api.attachments import format_attachment_block

        out = format_attachment_block([self._row(text="")])
        self.assertIn("(no extractable text)", out)


if __name__ == "__main__":
    unittest.main()
