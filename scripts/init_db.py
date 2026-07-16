import asyncio
import sys
import os

# Add the root project directory to sys.path so app modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import engine
from app.core.models import Base
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import text

async def init_db():
    print("Initializing database...")
    
    # 1. Create all our tables (idempotent, won't recreate if exists)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("Ensured application tables exist:")
        for table in Base.metadata.sorted_tables:
            print(f"  - {table.name}")
            
    # 2. Create apscheduler_jobs table using a synchronous engine on the same file
    # We choose the approach to create it here explicitly using a throwaway synchronous SQLAlchemyJobStore.
    from app.core.db import DATABASE_URL
    
    # SQLAlchemyJobStore uses sync sqlalchemy, so we replace the aiosqlite scheme if present
    sync_url = DATABASE_URL.replace("sqlite+aiosqlite", "sqlite")
    
    print(f"\nEnsuring APScheduler jobs table exists via {sync_url}...")
    jobstore = SQLAlchemyJobStore(url=sync_url)
    # The start() method inside JobStore will create the tables if they don't exist.
    jobstore.start(None, "default")
    jobstore.shutdown()
    
    print("  - apscheduler_jobs")
    
    # 3. Print final summary and verify using a raw query
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = [row[0] for row in result.fetchall()]
        
    print("\nInitialization Complete. All tables currently in the database:")
    for t in tables:
        print(f"  > {t}")
        
if __name__ == "__main__":
    asyncio.run(init_db())
