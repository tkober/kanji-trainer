"""subject illustrations

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

A separate table on purpose -- see `SubjectIllustration` in app/db.py. The
importer upserts `subjects` on every (re-)import and must never touch this
one, or a re-import would throw away every illustration fetched since.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db import UtcDateTime

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subject_illustrations",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("alt", sa.Text(), nullable=True),
        sa.Column("svg", sa.Text(), nullable=True),
        sa.Column("checked_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subject_id"),
    )


def downgrade() -> None:
    op.drop_table("subject_illustrations")
