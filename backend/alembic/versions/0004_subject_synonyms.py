"""subject synonyms

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07

A separate table, for the same reason as `subject_illustrations` -- see
`SubjectSynonyms` in app/db.py. The importer upserts `subjects` wholesale on
every (re-)import and must never touch this one, or a re-import would throw
away synonyms the learner typed in by hand.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db import JSONColumn, UtcDateTime

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subject_synonyms",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("synonyms", JSONColumn, server_default="[]", nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subject_id"),
    )


def downgrade() -> None:
    op.drop_table("subject_synonyms")
