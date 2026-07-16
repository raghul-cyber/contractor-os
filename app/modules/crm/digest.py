import os
from datetime import datetime, date
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.core.models import Run, Pipeline, PipelineStage
from app.modules.crm.transitions import notify_webhook

logger = logging.getLogger(__name__)

async def run_daily_digest(session: AsyncSession):
    today_str = datetime.utcnow().date().isoformat()
    
    # 1. Aggregate from Runs today
    runs_res = await session.execute(
        select(
            func.sum(Run.leads_scraped),
            func.sum(Run.leads_researched),
            func.sum(Run.emails_sent),
            func.sum(Run.replies_received)
        ).where(Run.started_at.like(f"{today_str}%"))
    )
    r = runs_res.one()
    leads_scraped = r[0] or 0
    leads_researched = r[1] or 0
    emails_sent = r[2] or 0
    replies_received = r[3] or 0
    
    # 2. Pipeline metrics
    pipe_res = await session.execute(
        select(Pipeline.stage, Pipeline.contract_value)
    )
    
    hot_leads = 0
    active_deals = 0
    total_value = 0.0
    
    for row in pipe_res.all():
        stage = row[0]
        val = row[1] or 0.0
        
        if stage in [PipelineStage.REPLIED.value, PipelineStage.MEETING_BOOKED.value]:
            hot_leads += 1
            
        if stage not in [PipelineStage.WON.value, PipelineStage.LOST.value, PipelineStage.PAUSED.value]:
            active_deals += 1
            total_value += val
            
    # 3. Format Digest
    digest_text = (
        f"📊 Daily Digest: {today_str}\n"
        f"----------------------------------------\n"
        f"Leads Scraped:      {leads_scraped}\n"
        f"Profiles Generated: {leads_researched}\n"
        f"Emails Sent:        {emails_sent}\n"
        f"Replies Received:   {replies_received}\n"
        f"----------------------------------------\n"
        f"Hot Leads:          {hot_leads}\n"
        f"Active Deals:       {active_deals}\n"
        f"Pipeline Value:     ${total_value:,.2f}\n"
    )
    
    # 4. Write to logs
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/digest_{today_str}.txt", "w", encoding="utf-8") as f:
        f.write(digest_text)
        
    # 5. Notify Webhook
    await notify_webhook(digest_text)
    
    return digest_text
