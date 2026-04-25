"""initial schema — feedback, cases, consultations, vector_embeddings, case_attachments

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-23
"""
from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id          SERIAL PRIMARY KEY,
            rating      TEXT        NOT NULL CHECK (rating IN ('up','down')),
            comment     TEXT        NOT NULL DEFAULT '',
            symptoms    TEXT        NOT NULL DEFAULT '',
            diagnosis   TEXT        NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cases (
            id          TEXT        PRIMARY KEY,
            user_id     TEXT        NOT NULL DEFAULT '',
            title       TEXT        NOT NULL DEFAULT '',
            state       JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS consultations (
            id           TEXT        PRIMARY KEY,
            user_id      TEXT        NOT NULL,
            case_id      TEXT        NOT NULL,
            summary      TEXT        NOT NULL,
            primary_dx   TEXT,
            icd_code     TEXT,
            urgency      TEXT,
            confidence   INTEGER,
            case_state   JSONB       NOT NULL DEFAULT '{}'::jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_consultations_user ON consultations(user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_embeddings (
            id         TEXT  PRIMARY KEY,
            user_id    TEXT,
            embedding  vector,
            metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
            document   TEXT  NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_vector_user ON vector_embeddings(user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_attachments (
            id             TEXT        PRIMARY KEY,
            case_id        TEXT        NOT NULL,
            user_id        TEXT        NOT NULL,
            kind           TEXT        NOT NULL,
            filename       TEXT,
            mime_type      TEXT,
            blob           BYTEA,
            text           TEXT        NOT NULL DEFAULT '',
            size_bytes     INTEGER     NOT NULL DEFAULT 0,
            question_index INTEGER,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_attachments_case ON case_attachments(case_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS case_attachments")
    op.execute("DROP TABLE IF EXISTS vector_embeddings")
    op.execute("DROP TABLE IF EXISTS consultations")
    op.execute("DROP TABLE IF EXISTS cases")
    op.execute("DROP TABLE IF EXISTS feedback")
    # Leave the `vector` extension in place — other databases in the same cluster may depend on it.
