import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import Lead, ActivityLog
from sqlalchemy import select

async def run():
    async with get_session() as s:
        res = await s.execute(select(Lead))
        leads = res.scalars().all()
        for l in leads:
            print(f"{l.company_name} - Status: {l.status} - Fit: {l.fit_score}")
            logs_res = await s.execute(select(ActivityLog).where(ActivityLog.lead_id == l.id))
            for log in logs_res.scalars().all():
                print(f"  Log: {log.action}")

if __name__ == "__main__":
    asyncio.run(run())
