import asyncio
import json
from sqlalchemy import select, update
from app.core.logger import get_logger
from app.core.config_loader import get_config
from app.core.db import get_session
from app.core.models import Run, ActivityLog, Lead
from app.core.llm_router import LLMRouter, RouterConfig

from .scrapers.website import scrape_website
from .scrapers.linkedin import scrape_linkedin_company
from .scrapers.news import scrape_google_news
from .synthesizer import synthesize_profile
from .fit_scorer import score_fit

logger = get_logger(__name__)

async def _process_lead(lead, router: LLMRouter, config, session) -> bool:
    """Processes a single lead. Returns True if successfully researched/low_fit."""
    try:
        # Concurrent Scraping
        website_task = asyncio.create_task(scrape_website(lead.website or lead.domain))
        linkedin_task = asyncio.create_task(scrape_linkedin_company(lead.company_name, lead.website or lead.domain))
        news_task = asyncio.create_task(scrape_google_news(lead.company_name))
        
        scraped_website, scraped_linkedin, scraped_news = await asyncio.gather(
            website_task, linkedin_task, news_task, return_exceptions=True
        )
        
        # Handle exceptions gracefully if tasks blew up instead of returning safe values
        if isinstance(scraped_website, Exception):
            logger.warning(f"Website task blew up for {lead.company_name}: {scraped_website}")
            scraped_website = {}
        if isinstance(scraped_linkedin, Exception):
            logger.warning(f"LinkedIn task blew up for {lead.company_name}: {scraped_linkedin}")
            scraped_linkedin = None
        if isinstance(scraped_news, Exception):
            logger.warning(f"News task blew up for {lead.company_name}: {scraped_news}")
            scraped_news = []
            
        # Synthesize
        try:
            profile = await synthesize_profile(lead, scraped_website, scraped_linkedin, scraped_news, router)
            
            # Score
            fit_score = score_fit(profile, config.targets)
            
            # Update Lead
            lead.profile_json = profile.model_dump_json()
            lead.decision_maker_name = profile.decision_maker
            lead.decision_maker_email = profile.decision_maker_email
            lead.decision_maker_title = profile.decision_maker_title
            lead.fit_score = fit_score
            
            min_score = config.system.profiler.min_fit_score
            if fit_score < min_score:
                lead.status = "LOW_FIT"
                session.add(ActivityLog(lead_id=lead.id, actor="profiler", action=f"Profiled as LOW_FIT (score {fit_score})"))
            else:
                lead.status = "RESEARCHED"
                session.add(ActivityLog(lead_id=lead.id, actor="profiler", action=f"Profiled successfully (score {fit_score})"))
                
            return True
            
        except ValueError as ve:
            # LLM completely failed JSON parsing after retries
            lead.status = "PROFILE_FAILED"
            session.add(ActivityLog(lead_id=lead.id, actor="profiler", action="Profile synthesis JSON failed", detail=str(ve)[:500]))
            return False
            
    except Exception as e:
        logger.error(f"Catastrophic failure processing lead {lead.company_name}: {e}")
        session.add(ActivityLog(lead_id=lead.id, actor="profiler", action="Catastrophic profiling failure", detail=str(e)))
        return False


async def run_profiler(state: dict) -> dict:
    """
    LangGraph entry point for the Profiler module.
    Pulls RAW leads and processes them concurrently.
    """
    logger.info("Starting Profiler module")
    config = get_config()
    
    # Init router
    router = LLMRouter(RouterConfig())
    
    run_id = state.get("run_id")
    batch_size = config.system.batch_size
    concurrency = config.system.profiler.concurrent_scrapes
    semaphore = asyncio.Semaphore(concurrency)
    
    successful_research = 0
    
    async with get_session() as session:
        # Pull RAW leads
        leads_res = await session.execute(
            select(Lead).where(Lead.status == "RAW").limit(batch_size)
        )
        leads = leads_res.scalars().all()
        
        if not leads:
            logger.info("No RAW leads found to profile.")
            return state
            
        async def _bound_process(lead):
            async with semaphore:
                return await _process_lead(lead, router, config, session)
                
        # Run concurrently
        tasks = [_bound_process(l) for l in leads]
        results = await asyncio.gather(*tasks)
        
        successful_research = sum(1 for r in results if r)
        
        # Update run stats
        if run_id:
            await session.execute(
                update(Run).where(Run.id == run_id).values(
                    leads_researched=Run.leads_researched + successful_research
                )
            )

        await session.commit()
    
    logger.info(f"Profiler module finished: {successful_research}/{len(leads)} leads researched.")
    
    return {
        **state,
        "profiler_processed": len(leads),
        "profiler_successful": successful_research
    }
