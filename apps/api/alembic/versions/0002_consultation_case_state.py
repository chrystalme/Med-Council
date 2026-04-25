"""store consultation case-state snapshots

Revision ID: 0002_consultation_case_state
Revises: 0001_initial
Create Date: 2026-04-25
"""
from __future__ import annotations

from alembic import op

revision = "0002_consultation_case_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE consultations
        ADD COLUMN IF NOT EXISTS case_state JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE consultations DROP COLUMN IF EXISTS case_state")
