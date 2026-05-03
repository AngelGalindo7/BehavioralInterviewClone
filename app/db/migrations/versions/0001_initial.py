"""Initial schema: pgvector extension, anecdotes, interview_sessions, turns.

Revision ID: 0001
Revises:
Create Date: 2026-04-26
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE IF NOT EXISTS anecdotes (
            id          SERIAL PRIMARY KEY,
            content     TEXT        NOT NULL,
            source_file VARCHAR(512) NOT NULL DEFAULT '',
            embedding   vector(1536) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at   TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS turns (
            id                 SERIAL PRIMARY KEY,
            session_id         UUID        NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
            sequence           INTEGER     NOT NULL,
            question_text      TEXT        NOT NULL,
            response_text      TEXT        NOT NULL,
            openai_response_id VARCHAR(128),
            ttfb_ms            DOUBLE PRECISION,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS turns_session_id_idx ON turns (session_id)")

    # IVFFlat index — created here but only becomes useful AFTER bulk ingestion.
    # The ingestion CLI (ingestion/ingest.py --recreate-index) will drop and
    # rebuild this index post-load with better centroid quality, then run
    # VACUUM ANALYZE anecdotes.
    #
    # lists=100 is appropriate for up to ~10,000 anecdotes (sqrt(10000)=100).
    # Tune upward if the knowledge base grows beyond 50k rows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS anecdotes_embedding_ivfflat_idx
        ON anecdotes
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS anecdotes_embedding_ivfflat_idx")
    op.execute("DROP TABLE IF EXISTS turns")
    op.execute("DROP TABLE IF EXISTS interview_sessions")
    op.execute("DROP TABLE IF EXISTS anecdotes")
    op.execute("DROP EXTENSION IF EXISTS vector")
