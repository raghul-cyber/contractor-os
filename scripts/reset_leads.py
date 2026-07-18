import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.core.db import get_session
from app.core.models import Lead

async def reset_leads():
    print("Resetting test leads to RAW...")
    async with get_session() as session:
        from sqlalchemy import select, update
        res = await session.execute(select(Lead).where(Lead.source == 'test_cutover'))
        leads = res.scalars().all()
        for lead in leads:
            lead.status = "RAW"
            print(f"Reset {lead.company_name} to RAW")
        await session.commit()

if __name__ == "__main__":
    asyncio.run(reset_leads())
