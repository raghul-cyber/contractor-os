import os
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.core.models import Lead, EmailEvent, NegotiatorDraft
from app.core.config_loader import get_config
from app.core.llm_router import LLMRouter

logger = get_logger(__name__)

async def draft_reply(lead_id: int, incoming_message_text: str, session: AsyncSession, router: LLMRouter) -> dict:
    """
    Drafts a response to a REPLIED lead's message using the LLM and context.
    Writes the draft to NegotiatorDraft. NEVER sends directly.
    """
    config = get_config()
    
    # 1. Fetch Context
    res = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = res.scalar_one_or_none()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    events_res = await session.execute(
        select(EmailEvent)
        .where(EmailEvent.lead_id == lead_id)
        .order_by(EmailEvent.timestamp.asc())
    )
    events = events_res.scalars().all()
    
    email_thread_lines = []
    for evt in events:
        if evt.raw_snippet:
            email_thread_lines.append(f"[{evt.timestamp}] ({evt.event_type}): {evt.raw_snippet}")
        else:
            email_thread_lines.append(f"[{evt.timestamp}] ({evt.event_type})")
            
    email_thread = "\n".join(email_thread_lines)
    
    # 2. Load Prompts
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    try:
        with open(os.path.join(prompts_dir, "negotiator.txt"), "r", encoding="utf-8") as f:
            prompt_tmpl = f.read()
    except FileNotFoundError:
        logger.error("Negotiator prompt template not found.")
        raise
        
    my_profile = config.profile
    service_name = my_profile.services[0].name if my_profile.services else "Custom AI Solutions"
    service_description = my_profile.services[0].description if my_profile.services else ""
    service_price = my_profile.services[0].price_range if my_profile.services else "Custom Pricing"
    service_value_prop = my_profile.value_proposition if hasattr(my_profile, "value_proposition") else ""

    # 3. Format Prompt
    prompt = prompt_tmpl.format(
        company_name=lead.company_name,
        domain=lead.domain,
        profile_json=lead.profile_json or "{}",
        service_name=service_name,
        service_description=service_description,
        service_price=service_price,
        service_value_prop=service_value_prop,
        email_thread=email_thread,
        incoming_message_text=incoming_message_text
    )
    
    # 4. Call LLM
    response_text = await router.call(prompt, task_type="email_craft")
    
    # Clean markdown
    response_text = response_text.strip()
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    elif response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
        
    draft_data = json.loads(response_text.strip())
    
    draft_subject = draft_data.get("draft_subject", "Re: Your Inquiry")
    draft_body = draft_data.get("draft_body", "")
    suggested_stage = draft_data.get("suggested_next_pipeline_stage", "")
    requires_human = draft_data.get("requires_human_confirmation", False)
    
    # Programmatic check for price/discount commitments
    import re
    if re.search(r"[$£€]|%", draft_body):
        requires_human = True
    
    # 5. Persist Draft
    # Check if a draft already exists for this lead
    existing_res = await session.execute(select(NegotiatorDraft).where(NegotiatorDraft.lead_id == lead_id))
    existing_draft = existing_res.scalar_one_or_none()
    
    if existing_draft:
        existing_draft.draft_subject = draft_subject
        existing_draft.draft_body = draft_body
        existing_draft.suggested_next_stage = suggested_stage
        existing_draft.requires_human_confirmation = requires_human
    else:
        new_draft = NegotiatorDraft(
            lead_id=lead_id,
            draft_subject=draft_subject,
            draft_body=draft_body,
            suggested_next_stage=suggested_stage,
            requires_human_confirmation=requires_human
        )
        session.add(new_draft)
        
    await session.commit()
    
    return {
        "draft_subject": draft_subject,
        "draft_body": draft_body,
        "suggested_next_pipeline_stage": suggested_stage,
        "requires_human_confirmation": requires_human
    }
