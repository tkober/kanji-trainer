"""drop illustration rows cached from a template placeholder

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14

WaniKani's radical pages started shipping `<template>` blocks whose
`<wk-mnemonic-image>` copies are placeholders (`src=""`,
`aria-label="${mnemonicImageDescription}"`) ahead of the real element. The
scraper took the first one and cached "no illustration" -- `svg IS NULL`,
with the placeholder's label as `alt` -- for every radical viewed since, and
such a row is only rechecked after `RECHECK_AFTER_DAYS` (issue #73).

The placeholder label identifies exactly those rows. Deleting them lets the
next view of each radical fetch again with the fixed parser; a genuine "no
illustration" row never carries that label and is left alone.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM subject_illustrations WHERE svg IS NULL AND alt LIKE :placeholder"),
        {"placeholder": "${%"},
    )


def downgrade() -> None:
    # The deleted rows were a cache of a wrong answer -- nothing to restore.
    pass
