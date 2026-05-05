"""Backfill ended_at on legacy interview_sessions rows.

Until the frontend End-session flow + ws disconnect cancellation landed, no
codepath called DELETE /session, so every interview_sessions row is still open
(ended_at IS NULL). This skews any duration analytics and leaves the schema
implying "active" for sessions that have been dead for days.

We can't recover true end times, so we mark all open rows as closed at backfill
time. The WHERE clause makes the upgrade idempotent — re-runs are no-ops.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE interview_sessions SET ended_at = NOW() WHERE ended_at IS NULL"
    )


def downgrade() -> None:
    # The pre-backfill NULLs are unrecoverable; intentional no-op.
    pass
