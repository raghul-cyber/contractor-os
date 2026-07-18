import asyncio
import sys
import os
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import Lead

async def seed_leads():
    print("Seeding test leads...")
    async with get_session() as session:
        # Check if they exist to avoid duplicates
        from sqlalchemy import select
        res = await session.execute(select(Lead).where(Lead.domain == 'echo.com'))
        existing = res.scalars().all()
        if existing:
            print("Found Echo Global Logistics lead. Resetting to RAW.")
            for lead in existing:
                lead.status = "RAW"
            await session.commit()
            return

        leads = [
            Lead(
                company_name="Echo Global Logistics",
                domain="echo.com",
                status="RAW",
                source="test_logistics",
                email="ahilightfreelance@gmail.com"  # Route test emails to user's inbox
            )
        ]
        
        session.add_all(leads)
        await session.commit()
        print(f"Successfully injected {len(leads)} test logistics leads.")

if __name__ == "__main__":
    asyncio.run(seed_leads())
