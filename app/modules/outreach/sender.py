import asyncio
from datetime import datetime, date
from tenacity import retry, stop_after_attempt, wait_exponential
from sqlalchemy import select, func
from app.core.logger import get_logger
from app.core.config_loader import get_config
from app.core.db import get_session
from app.core.models import EmailEvent

logger = get_logger(__name__)

class DailyLimitReachedError(Exception):
    pass

class SendFailureError(Exception):
    pass

# Retry 3x over ~10 minutes.
# 10 minutes = 600 seconds. 
# wait_exponential with max=300, multiplier=10 gives approx intervals: 10s, 20s, 40s...
# Let's just use fixed or exponential that adds up to ~600 if we wanted exactly 10 min, 
# but the prompt says "retry 3x over 10 minutes". We'll do 3 attempts, waiting ~3-5 mins between them.
# wait_fixed(300) = 5 mins wait. 2 retries * 5 mins = 10 mins.
from tenacity import wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(300), reraise=True)
async def _execute_send(to: str, subject: str, body: str, backend: str):
    """
    Simulates sending through Resend or SMTP based on the backend setting.
    This will actually throw if network is down or credentials fail.
    We just mock it for now since we aren't pulling in real SDKs yet.
    """
    if backend == "resend":
        # Simulate resend call
        logger.debug(f"Attempting send via Resend to {to}")
        # raise Exception("Simulated Resend Network Error")
        return {"id": "resend_12345", "status": "sent"}
    else:
        # Simulate SMTP call
        logger.debug(f"Attempting send via SMTP to {to}")
        # raise Exception("Simulated SMTP Network Error")
        return {"id": "smtp_12345", "status": "sent"}


async def send_email(to: str, subject: str, body: str, dry_run: bool = False) -> dict:
    """
    Sends an email enforcing daily limits and dry_run.
    """
    config = get_config()
    
    # 1. Enforce Daily Limit (assume sending domain is just 'default' for now, or pull from config)
    if hasattr(config.system, "outreach") and hasattr(config.system.outreach, "daily_send_limit"):
        daily_limit = config.system.outreach.daily_send_limit
    else:
        daily_limit = 20
    
    async with get_session() as session:
        # We query how many 'sent' events occurred today
        today_str = date.today().isoformat()
        res = await session.execute(
            select(func.count(EmailEvent.id))
            .where(EmailEvent.event_type == "sent")
        )
        sent_today = res.scalar_one()
        logger.error(f"DEBUG LIMIT CHECK: {sent_today} >= {daily_limit}?")
        
        if sent_today >= daily_limit:
            logger.warning(f"Daily send limit ({daily_limit}) reached. Cannot send to {to}.")
            raise DailyLimitReachedError("Daily send limit reached")

    # 2. Check dry_run globally or explicitly
    if hasattr(config.system, "outreach") and hasattr(config.system.outreach, "dry_run"):
        system_dry_run = config.system.outreach.dry_run
    else:
        system_dry_run = False
    
    is_dry_run = dry_run or system_dry_run
    
    if is_dry_run:
        logger.info(f"DRY RUN: Would send email to {to} | Subject: {subject}")
        return {"id": "dry_run_000", "status": "sent", "dry_run": True}
            
    # 3. Execute Send with Retries
    if hasattr(config.system, "outreach") and hasattr(config.system.outreach, "send_backend"):
        backend = config.system.outreach.send_backend
    else:
        backend = "smtp"
    
    try:
        result = await _execute_send(to, subject, body, backend)
        return result
    except Exception as e:
        logger.error(f"Send failed permanently after 3 retries for {to}: {e}")
        raise SendFailureError(str(e))
