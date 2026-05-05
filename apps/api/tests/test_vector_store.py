"""Tests for the vector_store abstraction.

PostgresVectorStore is exercised against a stubbed psycopg-style connection
that records the SQL and returns canned rows; no real Postgres is needed.
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest.mock import patch


class _FakeCursor:
    """Minimal stand-in for psycopg's Cursor — only the bits we exercise."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeCon:
    """Records every execute() call and replies with a configurable row set."""

    def __init__(self, query_rows: list[dict[str, Any]] | None = None) -> None:
        self._next_rows = query_rows or []
        self.executed: list[tuple[str, Any]] = []
        self.commits = 0

    def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        self.executed.append((sql, params))
        # The query path is the only execute() the test reads back from;
        # everything else (CREATE, INSERT, DELETE) just needs the cursor shape.
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeCursor(self._next_rows)
        return _FakeCursor()

    def commit(self) -> None:
        self.commits += 1


# ── coerce_metadata ────────────────────────────────────────────────────────


class CoerceMetadataTest(unittest.TestCase):
    def test_dict_passthrough(self) -> None:
        from medai_api.vector_store import _coerce_metadata

        out = _coerce_metadata({"k": 1})
        self.assertEqual(out, {"k": 1})
        # New dict — caller mutating shouldn't affect input.
        out["x"] = 2
        self.assertNotIn("x", _coerce_metadata({"k": 1}))

    def test_json_string(self) -> None:
        from medai_api.vector_store import _coerce_metadata

        self.assertEqual(_coerce_metadata('{"a":1}'), {"a": 1})

    def test_invalid_json_returns_empty_dict(self) -> None:
        from medai_api.vector_store import _coerce_metadata

        self.assertEqual(_coerce_metadata("not-json"), {})

    def test_empty_or_none_returns_empty_dict(self) -> None:
        from medai_api.vector_store import _coerce_metadata

        self.assertEqual(_coerce_metadata(""), {})
        self.assertEqual(_coerce_metadata(None), {})


# ── PostgresVectorStore ────────────────────────────────────────────────────


class PostgresVectorStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        from medai_api.vector_store import PostgresVectorStore

        self.store = PostgresVectorStore()

    def test_ensure_schema_runs_create_table_and_index(self) -> None:
        con = _FakeCon()
        self.store.ensure_schema(con)
        sql_executed = [s for (s, _) in con.executed]
        self.assertTrue(any("CREATE TABLE" in s for s in sql_executed))
        self.assertTrue(any("CREATE INDEX" in s for s in sql_executed))
        self.assertEqual(con.commits, 1)

    def test_upsert_inserts_with_user_id_extracted_from_metadata(self) -> None:
        con = _FakeCon()
        self.store.upsert(
            con,
            id="con_1",
            embedding=[0.1, 0.2, 0.3],
            metadata={"user_id": "user_xyz", "primary_dx": "MI"},
            document="patient summary",
        )
        sql, params = con.executed[0]
        self.assertIn("INSERT INTO vector_embeddings", sql)
        # (id, user_id, embedding, metadata-as-json, document)
        self.assertEqual(params[0], "con_1")
        self.assertEqual(params[1], "user_xyz")
        self.assertEqual(params[2], [0.1, 0.2, 0.3])
        self.assertEqual(json.loads(params[3]), {"user_id": "user_xyz", "primary_dx": "MI"})
        self.assertEqual(params[4], "patient summary")
        self.assertEqual(con.commits, 1)

    def test_query_returns_hits_sorted_with_score_inversion(self) -> None:
        rows = [
            {"id": "c1", "metadata": '{"user_id":"u1","tag":"a"}', "document": "doc-1", "distance": 0.1},
            {"id": "c2", "metadata": {"user_id": "u1", "tag": "b"}, "document": "doc-2", "distance": 0.4},
        ]
        con = _FakeCon(query_rows=rows)
        hits = self.store.query(con, embedding=[0.0, 0.0, 0.0], top_k=2, where={"user_id": "u1"})

        self.assertEqual([h.id for h in hits], ["c1", "c2"])
        # score = 1 - distance, clamped at zero.
        self.assertAlmostEqual(hits[0].score, 0.9, places=4)
        self.assertAlmostEqual(hits[1].score, 0.6, places=4)
        # SQL constraint: user_id WHERE clause is added.
        sql, _ = con.executed[0]
        self.assertIn("WHERE user_id = %s", sql)

    def test_query_metadata_filter_drops_non_matching_rows(self) -> None:
        rows = [
            {"id": "c1", "metadata": {"user_id": "u1", "tag": "a"}, "document": "", "distance": 0.1},
            {"id": "c2", "metadata": {"user_id": "u1", "tag": "b"}, "document": "", "distance": 0.2},
        ]
        con = _FakeCon(query_rows=rows)
        hits = self.store.query(
            con,
            embedding=[0.0],
            top_k=2,
            where={"user_id": "u1", "tag": "b"},  # only c2 matches
        )
        self.assertEqual([h.id for h in hits], ["c2"])

    def test_query_empty_rows_returns_empty(self) -> None:
        con = _FakeCon(query_rows=[])
        self.assertEqual(self.store.query(con, embedding=[0.0], top_k=5), [])

    def test_query_clamps_negative_distance_to_zero_score(self) -> None:
        # Float instabilities can give -1e-16 — should clamp to 0, not produce >1.
        rows = [{"id": "x", "metadata": {}, "document": "", "distance": 1.5}]
        con = _FakeCon(query_rows=rows)
        hits = self.store.query(con, embedding=[0.0], top_k=1)
        self.assertEqual(hits[0].score, 0.0)

    def test_delete_runs_and_commits(self) -> None:
        con = _FakeCon()
        self.store.delete(con, "con_1")
        sql, params = con.executed[0]
        self.assertIn("DELETE FROM vector_embeddings", sql)
        self.assertEqual(params, ("con_1",))
        self.assertEqual(con.commits, 1)


# ── Factory + Vertex stub ──────────────────────────────────────────────────


class GetVectorStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        from medai_api import vector_store as _vs

        _vs._store = None
        self.addCleanup(lambda: setattr(_vs, "_store", None))

    def test_default_is_postgres(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VECTOR_STORE", None)
            from medai_api.vector_store import PostgresVectorStore, get_vector_store

            self.assertIsInstance(get_vector_store(), PostgresVectorStore)

    def test_explicit_vertex_returns_stub(self) -> None:
        with patch.dict(os.environ, {"VECTOR_STORE": "vertex"}):
            from medai_api.vector_store import VertexVectorSearchStore, get_vector_store

            store = get_vector_store()
            self.assertIsInstance(store, VertexVectorSearchStore)
            with self.assertRaises(NotImplementedError):
                store.upsert(None, id="x", embedding=[], metadata={}, document="")


if __name__ == "__main__":
    unittest.main()
