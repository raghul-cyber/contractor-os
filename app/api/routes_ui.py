from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from typing import Optional

from app.core.db import get_session
from app.core.models import Lead, Pipeline
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
            
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "stats": stats,
                "leads": leads,
                "deals": deals_with_lead,
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
            "lead_row.html",
            {
                "request": request,
                "lead": lead
            }
        )
