import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import EmailEvent, Lead
from sqlalchemy import select

async def watch_emails():
    print("==================================================")
    print("[LIVE] OUTREACH MONITOR: Watching for sent emails...")
    print("==================================================\n")
    
    seen_ids = set()
    
    while True:
        async with get_session() as s:
            res = await s.execute(
                select(EmailEvent, Lead.company_name, Lead.email)
                .join(Lead, EmailEvent.lead_id == Lead.id)
                .order_by(EmailEvent.timestamp.desc())
                .limit(20)
            )
            events = res.all()
            
            # Print new events from oldest to newest
            for event, company, lead_email in reversed(events):
                if event.id not in seen_ids:
                    seen_ids.add(event.id)
                    if event.event_type == "sent":
                        print(f"[SENT] Email successfully delivered to: {company} ({lead_email})")
                        ts_str = event.timestamp if isinstance(event.timestamp, str) else event.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                        print(f"   Time: {ts_str}")
                        print("-" * 50)
            
        await asyncio.sleep(5) # Poll every 5 seconds

if __name__ == "__main__":
    try:
        asyncio.run(watch_emails())
    except KeyboardInterrupt:
        print("\n[LIVE] Monitor stopped by user.")
