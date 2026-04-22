"""
Vector store abstraction.

Default local: SQLite BLOB column + numpy in-process cosine scan.
   - No new services, no onnxruntime/chromadb platform issues, works on any Python.
   - Realistic scale: free tier caps at 4 consultations, pro at ~thousands —
     linear scan is microseconds per user.
Prod (GCP): Vertex AI Vector Search (stub — implement on migration).

Swap via env var: VECTOR_STORE=sqlite|chroma|vertex. Default: sqlite.

The store lives in a table named `vector_embeddings` inside the shared
feedback.db. The caller supplies the sqlite Connection on each call so we
don't fight the lifespan of the main FastAPI connection.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

import numpy as np

log = logging.getLogger("medai.vector_store")


@dataclass
class Hit:
    id: str
    score: float
    metadata: dict[str, Any]
    document: str


class VectorStore(Protocol):
    def ensure_schema(self, con: sqlite3.Connection) -> None: ...
    def upsert(
        self,
        con: sqlite3.Connection,
        *,
        id: str,
        embedding: list[float],
        metadata: dict[str, Any],
        document: str,
    ) -> None: ...
    def query(
        self,
        con: sqlite3.Connection,
        *,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]: ...
    def delete(self, con: sqlite3.Connection, id: str) -> None: ...


def _pack(vec: Iterable[float]) -> bytes:
    arr = np.asarray(list(vec), dtype=np.float32)
    return arr.tobytes()


def _unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class SqliteVectorStore:
    """SQLite-backed vector store — BLOB embeddings + in-process cosine similarity.

    The table schema stores a JSON metadata blob and the document text
    alongside the embedding. Metadata filters (the `where` argument) are
    evaluated in Python after loading candidate rows — fine at our scale.
    """

    def ensure_schema(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_embeddings (
                id         TEXT PRIMARY KEY,
                user_id    TEXT,
                embedding  BLOB NOT NULL,
                metadata   TEXT NOT NULL DEFAULT '{}',
                document   TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # user_id index lets us scope scans per-user without scanning every row.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_vector_user ON vector_embeddings(user_id)"
        )
        con.commit()

    def upsert(
        self,
        con: sqlite3.Connection,
        *,
        id: str,
        embedding: list[float],
        metadata: dict[str, Any],
        document: str,
    ) -> None:
        user_id = str(metadata.get("user_id") or "")
        con.execute(
            """
            INSERT INTO vector_embeddings (id, user_id, embedding, metadata, document)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id = excluded.user_id,
                embedding = excluded.embedding,
                metadata = excluded.metadata,
                document = excluded.document
            """,
            (id, user_id, _pack(embedding), json.dumps(metadata), document),
        )
        con.commit()

    def query(
        self,
        con: sqlite3.Connection,
        *,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]:
        # If a user_id filter is supplied, push it to the SQL layer for a scoped scan.
        where = where or {}
        params: list[Any] = []
        sql = "SELECT id, embedding, metadata, document FROM vector_embeddings"
        if "user_id" in where:
            sql += " WHERE user_id = ?"
            params.append(str(where["user_id"]))

        rows = con.execute(sql, params).fetchall()
        if not rows:
            return []

        q = np.asarray(embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []

        candidates: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            vec = _unpack(row["embedding"])
            if vec.size == 0:
                continue
            meta = json.loads(row["metadata"] or "{}")
            # Apply any non-user_id filters in Python (we expect few, on small sets).
            matches = True
            for k, v in where.items():
                if k == "user_id":
                    continue
                if meta.get(k) != v:
                    matches = False
                    break
            if not matches:
                continue

            denom = q_norm * float(np.linalg.norm(vec))
            if denom == 0.0:
                continue
            score = float(np.dot(q, vec) / denom)
            candidates.append((score, row))

        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[: max(1, int(top_k))]

        return [
            Hit(
                id=row["id"],
                score=score,
                metadata=json.loads(row["metadata"] or "{}"),
                document=row["document"] or "",
            )
            for score, row in top
        ]

    def delete(self, con: sqlite3.Connection, id: str) -> None:
        con.execute("DELETE FROM vector_embeddings WHERE id = ?", (id,))
        con.commit()


class ChromaVectorStore:
    """Optional backend — requires chromadb + onnxruntime.

    Skipped by default because onnxruntime lacks wheels on some macOS x86_64
    combos. Enable via VECTOR_STORE=chroma if you're on a supported platform
    and want ANN-level performance for larger corpora.
    """

    _collection = None

    def __init__(self, collection_name: str = "consultations", path: str | None = None) -> None:
        self.collection_name = collection_name
        self.path = path or os.environ.get("CHROMA_PATH") or "./chroma_db"

    def _ensure(self):
        if ChromaVectorStore._collection is None:
            import chromadb  # type: ignore

            client = chromadb.PersistentClient(path=self.path)
            ChromaVectorStore._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return ChromaVectorStore._collection

    def ensure_schema(self, con: sqlite3.Connection) -> None:
        # Chroma manages its own persistence directory.
        self._ensure()

    def upsert(
        self,
        con: sqlite3.Connection,
        *,
        id: str,
        embedding: list[float],
        metadata: dict[str, Any],
        document: str,
    ) -> None:
        self._ensure().upsert(
            ids=[id],
            embeddings=[embedding],
            metadatas=[metadata],
            documents=[document],
        )

    def query(
        self,
        con: sqlite3.Connection,
        *,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]:
        chroma_where: dict[str, Any] | None = None
        if where:
            chroma_where = where if len(where) == 1 else {"$and": [{k: v} for k, v in where.items()]}
        result = self._ensure().query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=chroma_where,
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        out: list[Hit] = []
        for i, doc_id in enumerate(ids):
            dist = float(distances[i]) if i < len(distances) else 1.0
            out.append(
                Hit(
                    id=str(doc_id),
                    score=max(0.0, 1.0 - dist),
                    metadata=dict(metadatas[i] or {}),
                    document=str(documents[i] or ""),
                )
            )
        return out

    def delete(self, con: sqlite3.Connection, id: str) -> None:
        self._ensure().delete(ids=[id])


class VertexVectorSearchStore:
    """Stub — implement on GCP migration using Vertex AI Vector Search."""

    def ensure_schema(self, *args, **kwargs) -> None:
        raise NotImplementedError("VertexVectorSearchStore not yet implemented.")

    def upsert(self, *args, **kwargs) -> None:
        raise NotImplementedError("VertexVectorSearchStore not yet implemented.")

    def query(self, *args, **kwargs) -> list[Hit]:
        raise NotImplementedError("VertexVectorSearchStore not yet implemented.")

    def delete(self, *args, **kwargs) -> None:
        raise NotImplementedError("VertexVectorSearchStore not yet implemented.")


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is not None:
        return _store
    backend = (os.environ.get("VECTOR_STORE") or "sqlite").lower()
    if backend == "vertex":
        _store = VertexVectorSearchStore()
    elif backend == "chroma":
        _store = ChromaVectorStore()
    else:
        _store = SqliteVectorStore()
    return _store
