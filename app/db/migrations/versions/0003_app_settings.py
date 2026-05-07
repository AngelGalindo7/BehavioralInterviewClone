"""Add app_settings table for persistent key-value config (e.g. stories text).

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-06
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   VARCHAR(128) PRIMARY KEY,
            value TEXT         NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings")
