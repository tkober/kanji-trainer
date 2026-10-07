"""context sentences

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07

Adds `Subject.context_sentences` (issue #32) -- WaniKani's example usage
sentences for vocabulary and kana_vocabulary, empty everywhere else. Imported
and re-imported exactly like every other content column; see
`_flush_subjects` in app/importer.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db import JSONColumn

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subjects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("context_sentences", JSONColumn, server_default="[]", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("subjects", schema=None) as batch_op:
        batch_op.drop_column("context_sentences")
