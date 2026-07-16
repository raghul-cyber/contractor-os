from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import get_session
from app.core.models import Lead, OutreachSequence, EmailEvent
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/leads", tags=["leads"])

@router.get("")
async def get_leads(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    async with get_session() as session:
        stmt = select(Lead)
        if status:
            stmt = stmt.where(Lead.status == status)
        stmt = stmt.order_by(Lead.updated_at.desc()).limit(limit).offset(offset)
        
        result = await session.execute(stmt)
        leads = result.scalars().all()
        
        # Simple dict conversion
        return [
            {
                "id": l.id,
                "company_name": l.company_name,
                "domain": l.domain,
                "status": l.status,
                "fit_score": l.fit_score,
                "source": l.source,
                "created_at": l.created_at,
                "updated_at": l.updated_at,
            }
            for l in leads
        ]

@router.get("/{lead_id}")
async def get_lead(lead_id: int):
    async with get_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
            
        seq_result = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == lead_id))
        sequences = seq_result.scalars().all()
        
        events_result = await session.execute(
            select(EmailEvent)
            .where(EmailEvent.lead_id == lead_id)
            .order_by(EmailEvent.timestamp.desc())
        )
        events = events_result.scalars().all()
        
        import json
        profile_data = {}
        if lead.profile_json:
            try:
                profile_data = json.loads(lead.profile_json)
            except:
                pass
                
        return {
            "lead": {
                "id": lead.id,
                "company_name": lead.company_name,
                "domain": lead.domain,
                "website": lead.website,
                "status": lead.status,
                "fit_score": lead.fit_score,
                "decision_maker_name": lead.decision_maker_name,
                "decision_maker_email": lead.decision_maker_email,
                "decision_maker_title": lead.decision_maker_title,
                "created_at": lead.created_at,
                "updated_at": lead.updated_at,
            },
            "profile": profile_data,
            "sequences": [
                {
                    "id": s.id,
                    "sequence_type": s.sequence_type,
                    "subject": s.subject,
                    "body": s.body,
                    "status": s.status,
                    "scheduled_at": s.scheduled_at,
                    "sent_at": s.sent_at
                } for s in sequences
            ],
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "sentiment": e.sentiment,
                    "timestamp": e.timestamp
                } for e in events
            ]
        }

@router.post("/{lead_id}/approve-sequence")
async def approve_sequence(lead_id: int):
    async with get_session() as session:
        # Check lead exists and is DRAFTED
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
            
        if lead.status != "DRAFTED":
            raise HTTPException(status_code=400, detail=f"Lead status is {lead.status}, must be DRAFTED")
            
        # Check initial sequence exists and is 'draft'
        seq_res = await session.execute(
            select(OutreachSequence)
            .where(OutreachSequence.lead_id == lead_id)
            .where(OutreachSequence.sequence_type == "initial")
        )
        initial_seq = seq_res.scalar_one_or_none()
        
        if not initial_seq:
            raise HTTPException(status_code=404, detail="Initial sequence not found")
            
        if initial_seq.status != "draft":
            raise HTTPException(status_code=400, detail=f"Sequence status is {initial_seq.status}, must be draft")
            
        # Flip to approved
        initial_seq.status = "approved"
        await session.commit()
        
        return {"status": "success", "message": "Sequence approved for outreach"}
