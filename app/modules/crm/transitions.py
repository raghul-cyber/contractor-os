import logging
import os
import httpx
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Pipeline, PipelineStage, ActivityLog, OutreachSequence, Lead
from app.core.config_loader import get_config
from app.core.llm_router import LLMRouter

logger = logging.getLogger(__name__)

async def notify_webhook(message: str):
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID")
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")

    try:
        async with httpx.AsyncClient() as client:
            if telegram_token and telegram_chat:
                url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                await client.post(url, json={"chat_id": telegram_chat, "text": message})
            if discord_url:
                await client.post(discord_url, json={"content": message})
    except Exception as e:
        logger.warning(f"Failed to send webhook notification: {e}")

async def mark_replied(lead_id: int, sentiment: str, raw_snippet: str, session: AsyncSession):
    # 1. Cancel remaining followups
    seq_res = await session.execute(
        select(OutreachSequence)
        .where(OutreachSequence.lead_id == lead_id)
        .where(OutreachSequence.status.in_(["draft", "queued", "approved"]))
    )
    for seq in seq_res.scalars().all():
        seq.status = "cancelled"

    # 2. Handle neutral sentiment ambiguity
    final_stage = PipelineStage.REPLIED.value
    
    if sentiment.lower() == "neutral":
        config = get_config()
        router = LLMRouter(config)
        prompt = (
            f"Classify this email reply snippet as either 'soft-yes' (interested, "
            f"needs more info, passing to colleague) or 'polite-no' (not interested, "
            f"unsubscribe, timing is bad). Snippet: {raw_snippet[:500]}\n"
            f"Reply with exactly one word: soft-yes or polite-no."
        )
        try:
            decision = await router.call(prompt, task_type="orchestration_decision")
            if "polite-no" in decision.lower():
                final_stage = PipelineStage.LOST.value
        except Exception as e:
            logger.warning(f"Ambiguous reply router decision failed for lead {lead_id}: {e}")
            final_stage = PipelineStage.REPLIED.value # default to keep active
            
    elif sentiment.lower() == "negative" or sentiment.lower() == "ooo":
        final_stage = PipelineStage.LOST.value if sentiment.lower() == "negative" else PipelineStage.PAUSED.value

    # 3. Create or Update Pipeline row
    pipe_res = await session.execute(select(Pipeline).where(Pipeline.lead_id == lead_id))
    pipeline = pipe_res.scalar_one_or_none()
    
    if pipeline:
        pipeline.stage = final_stage
    else:
        pipeline = Pipeline(lead_id=lead_id, stage=final_stage)
        session.add(pipeline)
        
    # Update lead status
    lead_res = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = lead_res.scalar_one_or_none()
    if lead:
        lead.status = "REPLIED"

    # 4. Activity Log
    session.add(ActivityLog(
        lead_id=lead_id,
        actor="crm",
        action="Lead replied",
        detail=f"Sentiment: {sentiment}. Stage set to {final_stage}."
    ))

    # 5. Notify
    if lead:
        await notify_webhook(f"📬 New Reply from {lead.company_name}!\nSentiment: {sentiment}\nStage: {final_stage}\nSnippet: {raw_snippet[:200]}...")
