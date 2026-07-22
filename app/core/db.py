import os
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.logger import get_logger

logger = get_logger(__name__)

# Ensure data/ directory exists
Path("data").mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/contractor_os.db")

engine = create_async_engine(DATABASE_URL, echo=False, connect_args={"timeout": 30.0})


def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    Fires on every new raw sqlite3 connection (not per-session).
    Sets PRAGMAs that must be active for every connection:
      - WAL mode: allows concurrent readers alongside a single writer
      - synchronous=NORMAL: safe durability/performance tradeoff under WAL
      - busy_timeout=5000: wait up to 5s for a lock instead of immediately raising
      - foreign_keys=ON: enforce FK constraints (off by default in SQLite)
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()
    logger.debug("SQLite PRAGMAs set: WAL, synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON")


# Register the listener on the underlying sync engine that aiosqlite delegates to
event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)


async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session():
    async with async_session_maker() as session:
        yield session


async def verify_pragmas() -> dict:
    """
    Query current PRAGMA values and return them for verification.
    Useful for startup checks and the acceptance-criteria test script.
    """
    async with engine.connect() as conn:
        journal = (await conn.execute(text("PRAGMA journal_mode;"))).scalar()
        synchronous = (await conn.execute(text("PRAGMA synchronous;"))).scalar()
        busy_timeout = (await conn.execute(text("PRAGMA busy_timeout;"))).scalar()
        foreign_keys = (await conn.execute(text("PRAGMA foreign_keys;"))).scalar()
    return {
        "journal_mode": journal,
        "synchronous": synchronous,
        "busy_timeout": busy_timeout,
        "foreign_keys": foreign_keys,
    }
