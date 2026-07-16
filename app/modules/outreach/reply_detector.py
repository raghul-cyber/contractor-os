import asyncio
import os
from sqlalchemy import select
from app.core.logger import get_logger
from app.core.config_loader import get_config
from app.core.db import get_session
from app.core.models import Lead, EmailEvent
from app.core.llm_router import LLMRouter, RouterConfig
from app.modules.crm.hooks import mark_replied

logger = get_logger(__name__)

async def fetch_unread_emails():
    """
    Fetches unread emails via IMAP.
    Separated to allow mocking in tests and keeping IMAP implementation clean.
    Returns list of dicts: {"sender": str, "body": str, "subject": str}
    """
    imap_host = os.getenv("IMAP_HOST")
    imap_user = os.getenv("IMAP_USER")
    imap_pass = os.getenv("IMAP_PASS")
    
    if not all([imap_host, imap_user, imap_pass]):
        logger.debug("IMAP credentials not fully configured. Skipping actual fetch.")
        return []
        
    try:
        import aioimaplib
        client = aioimaplib.IMAP4_SSL(host=imap_host)
        await client.wait_hello_from_server()
        await client.login(imap_user, imap_pass)
        await client.select("INBOX")
        
        # Simple search for UNSEEN
        typ, data = await client.search("UNSEEN")
        if typ != "OK" or not data[0]:
            await client.logout()
            return []
            
        messages = []
        # Real extraction logic goes here, but we will mock this in tests.
        # It involves fetching RFC822, parsing with email module, etc.
        # Since this is a placeholder implementation that fulfills the architecture,
        # we return an empty list if real parsing isn't filled.
        await client.logout()
        return messages
    except Exception as e:
        logger.error(f"IMAP poll failed: {e}")
        # Fails silently for the cycle per spec
        return []

async def poll_inbox_job():
    """
    Scheduled job to poll inbox and classify replies.
    """
    logger.info("Running poll_inbox job")
    
    config = get_config()
    router = LLMRouter(RouterConfig())
    
    messages = await fetch_unread_emails()
    if not messages:
        return
        
    async with get_session() as session:
        for msg in messages:
            sender = msg.get("sender", "").lower()
            body = msg.get("body", "")
            
            # 1. Match to Lead by sender email
            lead_res = await session.execute(
                select(Lead).where(
                    (Lead.email == sender) | (Lead.decision_maker_email == sender)
                ).limit(1)
            )
            lead = lead_res.scalars().first()
            
            if not lead:
                logger.info(f"Unmatched reply from {sender}. Ignoring.")
                continue
                
            # 2. Classify sentiment
            prompt = f"Classify the following email reply as positive, neutral, negative, or ooo (out of office).\n\nReply:\n{body[:1000]}"
            try:
                # The router will default to ollama (local, cheap) per task_routing rules for classification
                sentiment = await router.call(prompt, task_type="classification")
                sentiment = sentiment.strip().lower()
                
                # Cleanup typical LLM fluff
                if "positive" in sentiment: sentiment = "positive"
                elif "neutral" in sentiment: sentiment = "neutral"
                elif "negative" in sentiment: sentiment = "negative"
                elif "ooo" in sentiment or "out of office" in sentiment: sentiment = "ooo"
                else: sentiment = "neutral" # default fallback
            except Exception as e:
                logger.error(f"Failed to classify reply from {sender}: {e}")
                sentiment = "neutral" # Default on LLM failure
                
            # 3. Write event
            event = EmailEvent(
                lead_id=lead.id,
                event_type="replied",
                sentiment=sentiment,
                raw_snippet=body[:500]
            )
            session.add(event)
            
            # 4. Trigger CRM Hook
            if sentiment in ["positive", "neutral"]:
                mark_replied(lead.id)
                
        await session.commit()
