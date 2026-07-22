import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import Lead, OutreachSequence
from sqlalchemy import select

async def reset_drafts():
    async with get_session() as session:
        # Reset any DRAFTED leads back to RESEARCHED so they get redrafted
        res = await session.execute(select(Lead).where(Lead.status == "DRAFTED"))
        leads = res.scalars().all()
        for lead in leads:
            lead.status = "RESEARCHED"
        
        # We could also delete their old sequences just in case, but they will be overwritten by on_conflict_do_update
        await session.commit()
        print(f"Reset {len(leads)} leads from DRAFTED to RESEARCHED to apply new templates.")

if __name__ == "__main__":
    asyncio.run(reset_drafts())
