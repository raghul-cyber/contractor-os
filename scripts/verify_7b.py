import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.models import Base
from app.modules.signals.reddit_listener import poll_reddit_signals

async def main():
    print("Verifying 7B: Reddit Signal Listener...")

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        print("SKIP: Real Reddit credentials not found. Cannot perform real query.")
        return

    # In-memory DB for verification
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as session:
        print("Polling Reddit (read-only) using real credentials...")
        await poll_reddit_signals(session)
        print("Poll complete. Verify no errors occurred and hits were successfully stored.")

if __name__ == "__main__":
    asyncio.run(main())
