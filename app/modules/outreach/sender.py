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
async def _execute_send(to: str, subject: str, body: str, backend: str, sending_identity: str):
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
        logger.debug(f"Attempting real send via SMTP to {to}")
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import os
        
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        
        if not all([smtp_host, smtp_port, smtp_user, smtp_pass]):
            logger.error("Missing SMTP credentials in .env")
            raise SendFailureError("Missing SMTP credentials")
            
        msg = MIMEMultipart()
        msg["From"] = sending_identity
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        
        try:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            server.quit()
            logger.info(f"Successfully sent email via SMTP to {to}")
            return {"id": f"smtp_{int(datetime.utcnow().timestamp())}", "status": "sent"}
        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            raise SendFailureError(f"SMTP Error: {e}")


async def send_email(to: str, subject: str, body: str, sending_identity: str, dry_run: bool = False) -> dict:
    """
    Sends an email enforcing daily limits per identity and dry_run.
    """
    config = get_config()
    
    # 1. Find the daily limit for this specific identity
    daily_limit = 15 # default fallback
    if hasattr(config, "outreach") and hasattr(config.outreach, "sending_identities"):
        for identity in config.outreach.sending_identities:
            if identity.email == sending_identity:
                daily_limit = identity.daily_send_limit
                break
    
    async with get_session() as session:
        # We query how many 'sent' events occurred today for this identity
        today_str = date.today().isoformat()
        res = await session.execute(
            select(func.count(EmailEvent.id))
            .where(EmailEvent.event_type == "sent")
            .where(func.date(EmailEvent.timestamp) == today_str)
            .where(EmailEvent.sending_identity == sending_identity)
        )
        sent_today = res.scalar_one()
        
        if sent_today >= daily_limit:
            logger.warning(f"Daily send limit ({daily_limit}) reached for identity {sending_identity}. Cannot send to {to}.")
            raise DailyLimitReachedError("Daily send limit reached")

    # 2. Check dry_run globally or explicitly
    if hasattr(config.system, "outreach") and hasattr(config.system.outreach, "dry_run"):
        system_dry_run = config.system.outreach.dry_run
    else:
        system_dry_run = False
    
    is_dry_run = dry_run or system_dry_run
    
    if is_dry_run:
        logger.info(f"DRY RUN: Would send email to {to} from {sending_identity} | Subject: {subject}")
        return {"id": "dry_run_000", "status": "sent", "dry_run": True}
            
    # 3. Execute Send with Retries
    if hasattr(config.system, "outreach") and hasattr(config.system.outreach, "send_backend"):
        backend = config.system.outreach.send_backend
    else:
        backend = "smtp"
    
    try:
        result = await _execute_send(to, subject, body, backend, sending_identity)
        return result
    except Exception as e:
        logger.error(f"Send failed permanently after 3 retries for {to}: {e}")
        raise SendFailureError(str(e))
