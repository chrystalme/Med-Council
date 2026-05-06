"""
Vector store abstraction.

Default (local + Cloud SQL): **Postgres + pgvector**. Embeddings live in a
`vector_embeddings` table with a `vector` column; similarity uses pgvector's
cosine-distance operator (`<=>`), which ORDER BY turns into top-k with an
ANN index when one exists. For our scale a linear scan is microseconds per
user — we omit the index.

GCP (later): Vertex AI Vector Search (stub — implement on migration).

Switch via `VECTOR_STORE=postgres|vertex`. Default: postgres.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("medai.vector_store")


@dataclass
class Hit:
    id: str
    score: float
    metadata: dict[str, Any]
    document: str


class VectorStore(Protocol):
    def ensure_schema(self, con) -> None: ...
    def upsert(
        self,
        con,
        *,
        id: str,
        embedding: list[float],
        metadata: dict[str, Any],
        document: str,
    ) -> None: ...
    def query(
        self,
        con,
        *,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]: ...
    def delete(self, con, id: str) -> None: ...


def _coerce_metadata(raw) -> dict[str, Any]:
    """JSONB reads come back as dict; legacy TEXT reads as str. Handle both."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


class PostgresVectorStore:
    """Postgres + pgvector implementation.

    Cosine distance via the `<=>` operator. Score is returned as
    ``1 - distance`` so higher = more similar (keeps callers unchanged).
    """

    def ensure_schema(self, con) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_embeddings (
                id         TEXT PRIMARY KEY,
                user_id    TEXT,
                embedding  vector,
                metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
                document   TEXT NOT NULL DEFAULT ''
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_vector_user ON vector_embeddings(user_id)"
        )
        con.commit()

    def upsert(
        self,
        con,
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
            VALUES (%s, %s, %s::vector, %s::jsonb, %s)
            ON CONFLICT (id) DO UPDATE SET
                user_id   = EXCLUDED.user_id,
                embedding = EXCLUDED.embedding,
                metadata  = EXCLUDED.metadata,
                document  = EXCLUDED.document
            """,
            (id, user_id, embedding, json.dumps(metadata), document),
        )
        con.commit()

    def query(
        self,
        con,
        *,
        embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Hit]:
        where = where or {}
        sql = (
            "SELECT id, metadata, document, (embedding <=> %s::vector) AS distance "
            "FROM vector_embeddings"
        )
        params: list[Any] = [embedding]
        if "user_id" in where:
            sql += " WHERE user_id = %s"
            params.append(str(where["user_id"]))
        sql += " ORDER BY distance ASC LIMIT %s"
        params.append(max(1, int(top_k) * 4))  # over-fetch to allow Python-side metadata filters

        rows = con.execute(sql, params).fetchall()
        if not rows:
            return []

        out: list[Hit] = []
        for row in rows:
            meta = _coerce_metadata(row["metadata"])
            ok = True
            for k, v in where.items():
                if k == "user_id":
                    continue
                if meta.get(k) != v:
                    ok = False
                    break
            if not ok:
                continue
            distance = float(row["distance"] or 0.0)
            score = max(0.0, 1.0 - distance)
            out.append(Hit(id=row["id"], score=score, metadata=meta, document=row["document"] or ""))
            if len(out) >= max(1, int(top_k)):
                break
        return out

    def delete(self, con, id: str) -> None:
        con.execute("DELETE FROM vector_embeddings WHERE id = %s", (id,))
        con.commit()


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
    backend = (os.environ.get("VECTOR_STORE") or "postgres").lower()
    if backend == "vertex":
        _store = VertexVectorSearchStore()
    else:
        _store = PostgresVectorStore()
    return _store
