"""
Shared test fixtures for ContractorOS tests.

Provides a helper to create properly-configured in-memory SQLite engines
with PRAGMA foreign_keys=ON, matching the production configuration in
app/core/db.py.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine


def create_test_engine():
    """
    Create an in-memory async SQLite engine with FK enforcement enabled.

    Production db.py sets PRAGMA foreign_keys=ON on every connection via
    an event listener. Test in-memory engines must do the same, otherwise
    tests pass with FK violations that would fail in production.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    return engine
