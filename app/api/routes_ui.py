from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from typing import Optional

from app.core.db import get_session
from app.core.models import Lead, Pipeline, NegotiatorDraft, SignalHit
from app.api.routes_runs import get_stats_today

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="app/api/templates")

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, status: Optional[str] = None):
    stats = await get_stats_today()
    
    async with get_session() as session:
        # Fetch Leads
        stmt = select(Lead)
        if status:
            stmt = stmt.where(Lead.status == status)
        stmt = stmt.order_by(Lead.updated_at.desc()).limit(100)
        
        leads_res = await session.execute(stmt)
        leads = leads_res.scalars().all()
        
        # Fetch Pipeline Deals
        pipe_res = await session.execute(select(Pipeline).join(Lead))
        pipeline_deals = pipe_res.scalars().all()
        
        # We need the company_name for the pipeline deals
        deals_with_lead = []
        for deal in pipeline_deals:
            l_res = await session.execute(select(Lead).where(Lead.id == deal.lead_id))
            l = l_res.scalar_one()
            deals_with_lead.append({"deal": deal, "company": l.company_name})
        # Fetch Signals
        sig_res = await session.execute(
            select(SignalHit)
            .order_by(SignalHit.created_at.desc())
            .limit(10)
        )
        signals = sig_res.scalars().all()
            
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "stats": stats,
                "leads": leads,
                "deals": deals_with_lead,
                "signals": signals,
                "current_status": status
            }
        )

@router.get("/ui/leads/{lead_id}/row", response_class=HTMLResponse)
async def get_lead_row(request: Request, lead_id: int):
    async with get_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        if not lead:
            return HTMLResponse("<tr><td colspan='5'>Lead not found</td></tr>", status_code=404)
            
        return templates.TemplateResponse(
            request=request,
            name="lead_row.html",
            context={
                "lead": lead
            }
        )

@router.get("/ui/leads/{lead_id}/sequence", response_class=HTMLResponse)
async def get_lead_sequence(request: Request, lead_id: int):
    from app.core.models import OutreachSequence
    async with get_session() as session:
        result = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == lead_id).order_by(OutreachSequence.id))
        sequences = result.scalars().all()
        if not sequences:
            return HTMLResponse("<div class='p-4 text-gray-500'>No email sequences generated for this lead.</div>")
            
        html = "<div class='p-4 space-y-4 border-l-4 border-blue-200'>"
        for idx, seq in enumerate(sequences):
            html += f"<div class='bg-white p-3 rounded shadow-sm'>"
            html += f"<div class='font-bold text-sm mb-1 capitalize'>{seq.sequence_type} (Step {idx+1}) - {seq.status}</div>"
            html += f"<div class='text-sm mb-2'><strong>Subject:</strong> {seq.subject}</div>"
            body_fmt = seq.body.replace('\\n', '<br>')
            html += f"<div class='text-sm text-gray-700 whitespace-pre-wrap'>{body_fmt}</div>"
            html += f"</div>"
        html += "</div>"
        
        return HTMLResponse(html)

@router.get("/ui/leads/{lead_id}/negotiator", response_class=HTMLResponse)
async def get_lead_negotiator(request: Request, lead_id: int):
    async with get_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        if not lead:
            return HTMLResponse("Lead not found", status_code=404)
            
        draft_res = await session.execute(select(NegotiatorDraft).where(NegotiatorDraft.lead_id == lead_id))
        draft = draft_res.scalar_one_or_none()
        
        return templates.TemplateResponse(
            request=request,
            name="negotiator_box.html",
            context={
                "lead": lead,
                "draft": draft
            }
        )

