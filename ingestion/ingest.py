#!/usr/bin/env python3
"""
Knowledge-base ingestion CLI.

Usage:
    python ingestion/ingest.py --dir ingestion/anecdotes --recreate-index

Run this locally (not on EC2) once per knowledge-base update.
Requires DATABASE_URL and OPENAI_API_KEY in environment or .env file.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # type: ignore[import-untyped]

load_dotenv()

from sqlalchemy import text

from app.db.engine import AsyncSessionLocal, engine
from app.db.models import Anecdote
from ingestion.chunker import load_and_chunk
from ingestion.embedder import batch_embed


async def bulk_insert(
    chunks: list[tuple[str, str]],
    embeddings: list[list[float]],
) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            rows = [
                Anecdote(content=content, source_file=source, embedding=embedding)
                for (content, source), embedding in zip(chunks, embeddings)
            ]
            session.add_all(rows)
    print(f"Inserted {len(rows)} anecdotes.")


async def recreate_ivfflat_index() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("DROP INDEX IF EXISTS anecdotes_embedding_ivfflat_idx")
        )
        await conn.execute(
            text("""
                CREATE INDEX anecdotes_embedding_ivfflat_idx
                ON anecdotes
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """)
        )
        # VACUUM ANALYZE cannot run inside a transaction block — use AUTOCOMMIT mode
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text("VACUUM ANALYZE anecdotes"))
    print("IVFFlat index rebuilt and VACUUM ANALYZE complete.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest anecdotes into pgvector.")
    parser.add_argument(
        "--dir",
        default="ingestion/anecdotes",
        help="Directory containing .txt or .md anecdote files",
    )
    parser.add_argument(
        "--recreate-index",
        action="store_true",
        help="Drop and rebuild the IVFFlat index after ingestion (recommended)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of texts per OpenAI embedding API call",
    )
    args = parser.parse_args()

    chunks = load_and_chunk(args.dir)
    if not chunks:
        print(f"No .txt or .md files found in {args.dir!r}. Nothing to do.")
        return

    print(f"Found {len(chunks)} chunks from {args.dir!r}.")

    api_key = os.environ.get("OPENAI_API_KEY")
    embeddings = await batch_embed(chunks, batch_size=args.batch_size, api_key=api_key)

    await bulk_insert(chunks, embeddings)

    if args.recreate_index:
        await recreate_ivfflat_index()

    await engine.dispose()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
