"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-27

The baseline: exactly what `Base.metadata` (app/db.py) defines as of this
revision, `ADDED_COLUMNS` already folded in. A database that predates this --
which is every existing deployment -- never runs this file: `init_db` detects
the missing `alembic_version` table, finishes it with the (now frozen)
`migrate_schema()` bridge, and stamps straight to 0001 instead. This file only
runs for a database that starts from nothing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db import JSONColumn, UtcDateTime

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("wanikani_api_token", sa.Text(), nullable=True),
        sa.Column("wanikani_known_srs_stage", sa.Integer(), nullable=True),
        sa.Column("known_srs_stage", sa.Integer(), nullable=True),
        sa.Column("srs_interval_hours", sa.String(), nullable=True),
        sa.Column("daily_lesson_limit", sa.Integer(), nullable=True),
        sa.Column("lesson_batch_size", sa.Integer(), nullable=True),
        sa.Column("soft_answer_enabled", sa.Boolean(), nullable=True),
        sa.Column("review_item_order", sa.String(), nullable=True),
        sa.Column("review_type_order", sa.String(), nullable=True),
        sa.Column("updated_at", UtcDateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("id = 1", name="app_settings_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("wanikani_id", sa.Integer(), nullable=True),
        sa.Column("object_type", sa.String(), nullable=False),
        sa.Column("level", sa.Integer(), server_default="1", nullable=False),
        sa.Column("slug", sa.String(), server_default="", nullable=False),
        sa.Column("characters", sa.String(), nullable=True),
        sa.Column("character_image_url", sa.String(), nullable=True),
        sa.Column("meanings", JSONColumn, server_default="[]", nullable=False),
        sa.Column("auxiliary_meanings", JSONColumn, server_default="[]", nullable=False),
        sa.Column("readings", JSONColumn, server_default="[]", nullable=False),
        sa.Column("component_subject_ids", JSONColumn, server_default="[]", nullable=False),
        sa.Column("parts_of_speech", JSONColumn, server_default="[]", nullable=False),
        sa.Column("meaning_mnemonic", sa.Text(), server_default="", nullable=False),
        sa.Column("meaning_hint", sa.Text(), nullable=True),
        sa.Column("reading_mnemonic", sa.Text(), nullable=True),
        sa.Column("reading_hint", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", UtcDateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_subjects_wanikani", "subjects", ["wanikani_id"], unique=True
    )
    op.create_index("idx_subjects_order", "subjects", ["sort_order", "id"])
    op.create_index("idx_subjects_level_type", "subjects", ["level", "object_type"])

    op.create_table(
        "progress",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), server_default="new", nullable=False),
        sa.Column("srs_stage", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_review_at", UtcDateTime(), nullable=True),
        sa.Column("pending_meaning", sa.Boolean(), nullable=True),
        sa.Column("pending_reading", sa.Boolean(), nullable=True),
        sa.Column("session_incorrect", sa.Integer(), server_default="0", nullable=False),
        sa.Column("correct_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("incorrect_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lapses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", UtcDateTime(), nullable=True),
        sa.Column("passed_at", UtcDateTime(), nullable=True),
        sa.Column("burned_at", UtcDateTime(), nullable=True),
        sa.Column("known_at", UtcDateTime(), nullable=True),
        sa.Column("updated_at", UtcDateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subject_id"),
    )
    op.create_index("idx_progress_due", "progress", ["next_review_at"])
    op.create_index("idx_progress_state", "progress", ["state"])

    op.create_table(
        "review_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("question_type", sa.String(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("given_answer", sa.String(), server_default="", nullable=False),
        sa.Column("srs_stage_before", sa.Integer(), server_default="0", nullable=False),
        sa.Column("srs_stage_after", sa.Integer(), server_default="0", nullable=False),
        sa.Column("answered_at", UtcDateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_review_log_answered", "review_log", ["answered_at"])
    op.create_index("idx_review_log_subject", "review_log", ["subject_id", "answered_at"])

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(), server_default="running", nullable=False),
        sa.Column("known_srs_stage", sa.Integer(), server_default="5", nullable=False),
        sa.Column("subjects_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("subjects_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assignments_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("marked_known", sa.Integer(), server_default="0", nullable=False),
        sa.Column("marked_learning", sa.Integer(), server_default="0", nullable=False),
        sa.Column("marked_new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("message", sa.Text(), server_default="", nullable=False),
        sa.Column("started_at", UtcDateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_import_runs_started", "import_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("idx_import_runs_started", table_name="import_runs")
    op.drop_table("import_runs")

    op.drop_index("idx_review_log_subject", table_name="review_log")
    op.drop_index("idx_review_log_answered", table_name="review_log")
    op.drop_table("review_log")

    op.drop_index("idx_progress_state", table_name="progress")
    op.drop_index("idx_progress_due", table_name="progress")
    op.drop_table("progress")

    op.drop_index("idx_subjects_level_type", table_name="subjects")
    op.drop_index("idx_subjects_order", table_name="subjects")
    op.drop_index("idx_subjects_wanikani", table_name="subjects")
    op.drop_table("subjects")

    op.drop_table("app_settings")
