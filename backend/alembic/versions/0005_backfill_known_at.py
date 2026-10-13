"""backfill known_at from the first succeeded import

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13

`progress.known_at` is set by both the importer and `mark_known` today, but a
row created before that column existed predates it -- `state = 'known' AND
known_at IS NULL`. The closest available truth for such a row is the first
succeeded import's `finished_at`: before `known_at` existed, "known" could
only come from an import, there being no in-app "I know this" independent of
one yet. issue #61's growth reconstruction (`app/growth.py`) leans on
`known_at` being set for every known row, which is what makes this worth
fixing now rather than leaving as a historical gap.

A no-op if there is no succeeded import, or if every known row already
carries its own `known_at`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    first_import_finished_at = connection.execute(
        sa.text(
            "SELECT finished_at FROM import_runs WHERE status = 'succeeded' "
            "ORDER BY finished_at LIMIT 1"
        )
    ).scalar()
    if first_import_finished_at is None:
        return
    connection.execute(
        sa.text("UPDATE progress SET known_at = :finished_at WHERE state = 'known' AND known_at IS NULL"),
        {"finished_at": first_import_finished_at},
    )


def downgrade() -> None:
    # A data backfill, not a schema change -- nothing to reverse without
    # risking clobbering a `known_at` genuinely set since.
    pass
