import asyncio
import json
import os
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from app.core.logger import get_logger
from app.core.config_loader import get_config
from app.core.db import get_session
from app.core.models import Lead, ActivityLog, OutreachSequence
from app.core.llm_router import LLMRouter, RouterConfig

logger = get_logger(__name__)

def truncate_text(text: str, max_words: int) -> str:
    """Truncates text to a max word count, appending [TRUNCATED] if shortened."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " ... [TRUNCATED]"

async def run_craft(state: dict) -> dict:
    """
    LangGraph entry point for the Craft module.
    Pulls RESEARCHED leads, generates 4-email sequence via ONE LLM call.
    Validates limits programmatically.
    """
    logger.info("Starting Craft module")
    config = get_config()
    router = LLMRouter(RouterConfig())
    
    # Load Prompts
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    try:
        with open(os.path.join(prompts_dir, "initial_email.txt"), "r", encoding="utf-8") as f:
            initial_prompt_tmpl = f.read()
        with open(os.path.join(prompts_dir, "followups.txt"), "r", encoding="utf-8") as f:
            followups_prompt_tmpl = f.read()
    except FileNotFoundError as e:
        logger.error(f"Craft prompts missing: {e}")
        return state

    # Pull My Services Profile
    my_profile = config.profile
    
    async with get_session() as session:
        leads_res = await session.execute(
            select(Lead).where(Lead.status == "RESEARCHED").limit(config.system.batch_size)
        )
        leads = leads_res.scalars().all()
        
        if not leads:
            logger.info("No RESEARCHED leads found to craft.")
            return state
            
        successful = 0
        
        for lead in leads:
            try:
                # Format the 55 services into a condensed catalog string
                catalog_lines = []
                for s in config.catalog.services:
                    catalog_lines.append(f"- [{s.category}] {s.name}: {s.description}")
                service_catalog_str = "\n".join(catalog_lines)

                # 1. Compose Prompt
                initial_prompt = initial_prompt_tmpl.format(
                    service_catalog=service_catalog_str,
                    service_value_prop=my_profile.value_proposition,
                    company_name=lead.company_name,
                    domain=lead.domain,
                    profile_json=lead.profile_json or "{}"
                )
                full_prompt = initial_prompt + "\n\n" + followups_prompt_tmpl
                
                # 2. Call LLM once per lead
                response_text = await router.call(full_prompt, task_type="email_craft")
                
                # Clean markdown
                response_text = response_text.strip()
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                elif response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                    
                sequence_data = json.loads(response_text.strip())
                
                # 3. Validate and Truncate Word Counts
                seqs = []
                for seq_type, limits in [("initial", 150), ("fu1", 80), ("fu2", 80), ("fu3", 80)]:
                    if seq_type not in sequence_data:
                        raise ValueError(f"Missing {seq_type} in JSON")
                        
                    body = sequence_data[seq_type].get("body", "")
                    subj = sequence_data[seq_type].get("subject", "")
                    
                    truncated_body = truncate_text(body, limits)
                    seqs.append({
                        "lead_id": lead.id,
                        "sequence_type": seq_type,
                        "subject": subj,
                        "body": truncated_body,
                        "status": "draft"
                    })
                
                # 4. Upsert outreach_sequences
                for s in seqs:
                    stmt = insert(OutreachSequence).values(**s)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["lead_id", "sequence_type"],
                        set_={"subject": s["subject"], "body": s["body"], "status": "draft"}
                    )
                    await session.execute(stmt)
                    
                # 5. Update Lead Status
                lead.status = "DRAFTED"
                session.add(ActivityLog(lead_id=lead.id, actor="craft", action="Generated 4-email sequence"))
                successful += 1
                
            except Exception as e:
                logger.error(f"Failed to craft sequence for {lead.company_name}: {e}")
                lead.status = "CRAFT_FAILED"
                session.add(ActivityLog(lead_id=lead.id, actor="craft", action="Craft synthesis failed", detail=str(e)[:500]))
                
        await session.commit()
        
    return {
        **state,
        "craft_processed": len(leads),
        "craft_successful": successful
    }
