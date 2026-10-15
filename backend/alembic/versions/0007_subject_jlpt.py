"""subject jlpt level

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15

Adds `Subject.jlpt` (issue #62) -- nullable, no data touched here on purpose:
WaniKani has no JLPT data at all, so the column is filled from the checked-in
snapshot in `app/data/jlpt/` by `app.jlpt.refresh_jlpt()`, called after
`upgrade head` in `db.init_db()` and again at the end of every import -- see
that function's own docstring for why a data refresh deliberately stays out
of the migration itself.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subjects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("jlpt", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("subjects", schema=None) as batch_op:
        batch_op.drop_column("jlpt")
