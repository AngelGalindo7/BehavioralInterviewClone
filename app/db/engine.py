from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _build_engine():
    """
    SQLite (used in CI / Windows where asyncpg cannot be built) routes through
    StaticPool and rejects pool_size / max_overflow / pool_timeout. Production
    uses asyncpg over Postgres; pass the pool config there.
    """
    is_sqlite = settings.database_url.startswith("sqlite")
    kwargs = dict(echo=False, pool_pre_ping=True)
    if not is_sqlite:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_timeout"] = settings.db_pool_timeout
    return create_async_engine(settings.database_url, **kwargs)


engine = _build_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
