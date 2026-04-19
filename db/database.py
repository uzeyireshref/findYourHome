import os
from uuid import uuid4
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./emlak.db")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    # Supabase transaction pooler runs through PgBouncer; avoid asyncpg's
    # default prepared statement names/caches conflicting across pooled sessions.
    engine_kwargs.update(
        poolclass=NullPool,
        connect_args={
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        },
    )

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

async def _ensure_search_criteria_columns():
    async with engine.begin() as conn:
        def get_columns(sync_conn):
            inspector = inspect(sync_conn)
            return {column["name"] for column in inspector.get_columns("search_criteria")}

        existing_columns = await conn.run_sync(get_columns)

        if "is_furnished" not in existing_columns:
            await conn.execute(text("ALTER TABLE search_criteria ADD COLUMN is_furnished BOOLEAN"))

        if "seller_type" not in existing_columns:
            await conn.execute(text("ALTER TABLE search_criteria ADD COLUMN seller_type VARCHAR"))

        if "listing_type" not in existing_columns:
            await conn.execute(text("ALTER TABLE search_criteria ADD COLUMN listing_type VARCHAR"))

        if "property_type" not in existing_columns:
            await conn.execute(text("ALTER TABLE search_criteria ADD COLUMN property_type VARCHAR"))

async def init_db():
    from db import models  # noqa: F401 - register SQLAlchemy models before create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_search_criteria_columns()
