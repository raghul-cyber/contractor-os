import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import Lead

async def seed_leads():
    print("Seeding test leads...")
    async with get_session() as session:
        # Check if they exist to avoid duplicates
        from sqlalchemy import select
        res = await session.execute(select(Lead).where(Lead.source == 'test_cutover'))
        existing = res.scalars().all()
        if existing:
            print(f"Found {len(existing)} existing test leads. Skipping injection.")
            return

        leads = [
            Lead(
                company_name="OpenAI",
                domain="openai.com",
                status="RAW",
                source="test_cutover"
            ),
            Lead(
                company_name="Anthropic",
                domain="anthropic.com",
                status="RAW",
                source="test_cutover"
            )
        ]
        
        session.add_all(leads)
        await session.commit()
        print(f"Successfully injected {len(leads)} test leads.")

if __name__ == "__main__":
    asyncio.run(seed_leads())
