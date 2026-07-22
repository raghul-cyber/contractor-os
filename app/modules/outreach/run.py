import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, update, func
from app.core.logger import get_logger
from app.core.config_loader import get_config
from app.core.db import get_session
from app.core.models import Lead, OutreachSequence, ActivityLog, EmailEvent
from .sender import send_email, DailyLimitReachedError, SendFailureError

logger = get_logger(__name__)

async def _get_next_sending_identity(session, config) -> str:
    """Finds the sending identity with the most remaining capacity today."""
    if not hasattr(config, "outreach") or not hasattr(config.outreach, "sending_identities") or not config.outreach.sending_identities:
        return "default@ahixlight.com"
        
    today_str = datetime.utcnow().date().isoformat()
    
    # Get current counts for today
    res = await session.execute(
        select(EmailEvent.sending_identity, func.count(EmailEvent.id))
        .where(EmailEvent.event_type == "sent")
        .where(func.date(EmailEvent.timestamp) == today_str)
        .group_by(EmailEvent.sending_identity)
    )
    counts = dict(res.fetchall())
    
    best_identity = None
    most_capacity = -1
    
    for identity in config.outreach.sending_identities:
        used = counts.get(identity.email, 0)
        capacity = identity.daily_send_limit - used
        if capacity > most_capacity:
            most_capacity = capacity
            best_identity = identity.email
            
    if most_capacity <= 0 or not best_identity:
        raise DailyLimitReachedError("All sending identities have reached their daily limits.")
        
    return best_identity

async def run_outreach(state: dict) -> dict:
    """
    LangGraph entry point.
    Finds DRAFTED leads with eligible initial sequences.
    Sends initial, queues followups.
    """
    logger.info("Starting Outreach module")
    config = get_config()
    
    req_manual = getattr(config.system.craft, "require_manual_approval", True) if hasattr(config.system, "craft") else True
    allowed_seq_status = "approved" if req_manual else "draft"
    
    async with get_session() as session:
        # Find leads at DRAFTED
        leads_res = await session.execute(
            select(Lead).where(Lead.status == "DRAFTED").limit(config.system.batch_size)
        )
        leads = leads_res.scalars().all()
        
        successful_count = 0
        for lead in leads:
            try:
                # Find the initial sequence
                seq_res = await session.execute(
                    select(OutreachSequence)
                    .where(OutreachSequence.lead_id == lead.id)
                    .where(OutreachSequence.sequence_type == "initial")
                )
                initial_seq = seq_res.scalars().first()
                
                if not initial_seq or initial_seq.status != allowed_seq_status:
                    continue # Skip if not approved (or draft if manual=False)
                    
                # Pick identity
                chosen_identity = await _get_next_sending_identity(session, config)
                
                # Send
                res = await send_email(
                    to=lead.decision_maker_email or lead.email or "unknown@test.com",
                    subject=initial_seq.subject,
                    body=initial_seq.body,
                    sending_identity=chosen_identity,
                    dry_run=False,
                    session=session
                )
                
                now_str = datetime.utcnow().isoformat()
                initial_seq.status = "sent"
                initial_seq.sent_at = now_str
                initial_seq.sending_identity = chosen_identity
                
                # Queue followups
                if hasattr(config.system, "outreach") and hasattr(config.system.outreach, "follow_up_intervals_days"):
                    fu_intervals = config.system.outreach.follow_up_intervals_days
                else:
                    fu_intervals = [5, 10, 15]
                
                fu_res = await session.execute(
                    select(OutreachSequence)
                    .where(OutreachSequence.lead_id == lead.id)
                    .where(OutreachSequence.sequence_type.in_(["fu1", "fu2", "fu3"]))
                )
                followups = fu_res.scalars().all()
                
                fu_map = {f.sequence_type: f for f in followups}
                
                now = datetime.utcnow()
                if "fu1" in fu_map:
                    fu_map["fu1"].status = "queued"
                    fu_map["fu1"].scheduled_at = (now + timedelta(days=fu_intervals[0])).isoformat()
                    fu_map["fu1"].sending_identity = chosen_identity
                if "fu2" in fu_map:
                    fu_map["fu2"].status = "queued"
                    fu_map["fu2"].scheduled_at = (now + timedelta(days=fu_intervals[1])).isoformat()
                    fu_map["fu2"].sending_identity = chosen_identity
                if "fu3" in fu_map:
                    fu_map["fu3"].status = "queued"
                    fu_map["fu3"].scheduled_at = (now + timedelta(days=fu_intervals[2])).isoformat()
                    fu_map["fu3"].sending_identity = chosen_identity
                    
                # Advance Lead
                lead.status = "SENT"
                
                # Write EmailEvent
                session.add(EmailEvent(lead_id=lead.id, sequence_id=initial_seq.id, event_type="sent", sending_identity=chosen_identity))
                session.add(ActivityLog(lead_id=lead.id, actor="outreach", action="Sent initial email"))
                
                successful_count += 1
                
            except DailyLimitReachedError:
                logger.warning(f"Daily limit reached. Pausing outreach for lead {lead.id} until next cycle.")
                break # Stop processing this batch
            except SendFailureError:
                logger.error(f"Permanent send failure for lead {lead.id}. Marking sequence as failed.")
                initial_seq.status = "failed"
                session.add(ActivityLog(lead_id=lead.id, actor="outreach", action="Initial email send failed completely"))
            except Exception as e:
                logger.error(f"Error processing outreach for lead {lead.id}: {e}")
                
            # Commit per lead so EmailEvents are visible to subsequent sender queries
            await session.commit()
            
    return {
        **state,
        "outreach_processed": len(leads),
        "outreach_sent": successful_count
    }


async def check_due_followups_job():
    """
    APScheduler job to send due followups and check for ghosted leads.
    """
    logger.info("Running check_due_followups job")
    
    async with get_session() as session:
        # 1. Send Due Followups
        now_str = datetime.utcnow().isoformat()
        due_res = await session.execute(
            select(OutreachSequence)
            .join(Lead)
            .where(OutreachSequence.status == "queued")
            .where(OutreachSequence.scheduled_at <= now_str)
            .where(Lead.status.in_(["SENT", "FU1_SENT", "FU2_SENT"])) # Ensure they haven't replied or paused
        )
        due_seqs = due_res.scalars().all()
        
        for seq in due_seqs:
            try:
                # Fetch Lead
                lead_res = await session.execute(select(Lead).where(Lead.id == seq.lead_id))
                lead = lead_res.scalars().first()
                
                # Send
                identity_to_use = seq.sending_identity or "default@ahixlight.com"
                res = await send_email(
                    to=lead.decision_maker_email or lead.email or "unknown@test.com",
                    subject=seq.subject,
                    body=seq.body,
                    sending_identity=identity_to_use,
                    dry_run=False,
                    session=session
                )
                
                seq.status = "sent"
                seq.sent_at = datetime.utcnow().isoformat()
                
                # Advance Lead Status
                if seq.sequence_type == "fu1":
                    lead.status = "FU1_SENT"
                elif seq.sequence_type == "fu2":
                    lead.status = "FU2_SENT"
                elif seq.sequence_type == "fu3":
                    lead.status = "FU3_SENT"
                    
                session.add(EmailEvent(lead_id=lead.id, sequence_id=seq.id, event_type="sent", sending_identity=identity_to_use))
                session.add(ActivityLog(lead_id=lead.id, actor="outreach", action=f"Sent {seq.sequence_type} email"))
                
            except DailyLimitReachedError:
                logger.warning("Daily limit reached during followups. Pausing.")
                break
            except SendFailureError:
                seq.status = "failed"
                session.add(ActivityLog(lead_id=seq.lead_id, actor="outreach", action=f"{seq.sequence_type} send failed completely"))
            except Exception as e:
                logger.error(f"Error sending followup {seq.id}: {e}")
                
            await session.commit()
                
        # 2. Ghosted Check
        # Find leads at FU3_SENT where FU3 was sent > 5 days ago and no reply exists
        ghost_date_str = (datetime.utcnow() - timedelta(days=5)).isoformat()
        
        ghost_res = await session.execute(
            select(Lead)
            .join(OutreachSequence, (OutreachSequence.lead_id == Lead.id) & (OutreachSequence.sequence_type == "fu3"))
            .where(Lead.status == "FU3_SENT")
            .where(OutreachSequence.status == "sent")
            .where(OutreachSequence.sent_at <= ghost_date_str)
        )
        ghost_leads = ghost_res.scalars().all()
        
        for lead in ghost_leads:
            # Verify no replies exist (just to be safe, though they should be marked REPLIED if they did)
            reply_count_res = await session.execute(
                select(func.count(EmailEvent.id))
                .where(EmailEvent.lead_id == lead.id)
                .where(EmailEvent.event_type == "replied")
            )
            reply_count = reply_count_res.scalar_one()
            
            if reply_count == 0:
                lead.status = "GHOSTED"
                session.add(ActivityLog(lead_id=lead.id, actor="outreach", action="Lead marked GHOSTED"))
                
            await session.commit()
