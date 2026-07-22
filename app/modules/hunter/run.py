from app.core.logger import get_logger
from app.core.config_loader import get_config
from app.core.db import get_session
from app.core.models import Run, ActivityLog, Lead
from sqlalchemy import select, update
from dotenv import load_dotenv
load_dotenv()

from .sources.apify_maps import scrape_google_maps
from .sources.apify_jobboard_signals import scrape_job_boards
from .sources.apify_crunchbase_public import scrape_crunchbase
from .sources.directory_import import scrape_directories
from .sources.apify_contact import extract_contacts
from .sources.bs4_email_fallback import fast_extract_email
from .sources.apify_leadscraper import scrape_leads
from .sources.manual_import import import_from_csv
from .sources.local_search import scrape_local_search
from .dedup import insert_lead_if_new

logger = get_logger(__name__)

async def run_hunter(state: dict) -> dict:
    from dotenv import load_dotenv
    from pathlib import Path
    env_path = Path(__file__).parent.parent.parent.parent / '.env'
    load_dotenv(dotenv_path=env_path, override=True)
    
    """
    LangGraph entry point for the Hunter module.
    Calls sources in priority order: maps -> contact (backfill) -> leadscraper -> hunter.io -> manual
    """
    logger.info("Starting Hunter module")
    config = get_config()
    
    run_id = state.get("run_id")
    csv_path = state.get("csv_path")
    
    total_inserted = 0
    total_skipped = 0
    
    async with get_session() as session:
        # 1. Apify Maps
        try:
            filters = {
                "sectors": config.targets.targeting.sectors,
                "location": config.targets.targeting.locations[0] if config.targets.targeting.locations else "Global",
                "limit": 20
            }
            maps_results = await scrape_google_maps(filters)
            inserted_maps = 0
            for raw in maps_results:
                if await insert_lead_if_new(session, raw):
                    inserted_maps += 1
                else:
                    total_skipped += 1
            total_inserted += inserted_maps
            
            session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Apify Maps: {len(maps_results)} found, {inserted_maps} inserted"))
        except Exception as e:
            logger.error(f"Apify Maps source failed: {e}")
            session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Apify Maps failed: {e}"))
            
        # 2. Apify Job Board Signals
        if getattr(config.system.hunter, "use_jobboard_signals", False):
            try:
                jb_filters = {
                    "pain_signals": getattr(config.targets.targeting, "pain_signals", []),
                    "location": config.targets.targeting.locations[0] if getattr(config.targets.targeting, "locations", None) else "Global",
                    "limit": 5
                }
                jb_results = await scrape_job_boards(jb_filters)
                inserted_jb = 0
                for raw in jb_results:
                    if await insert_lead_if_new(session, raw):
                        inserted_jb += 1
                    else:
                        total_skipped += 1
                total_inserted += inserted_jb
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Job Boards: {len(jb_results)} found, {inserted_jb} inserted"))
            except Exception as e:
                logger.error(f"Apify Job Boards source failed: {e}")
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Job Boards failed: {e}"))
                
        # 3. Apify Crunchbase Public
        if getattr(config.system.hunter, "use_crunchbase", False):
            try:
                cb_filters = {
                    "sectors": config.targets.targeting.sectors,
                    "limit": 10
                }
                cb_results = await scrape_crunchbase(cb_filters)
                inserted_cb = 0
                for raw in cb_results:
                    if await insert_lead_if_new(session, raw):
                        inserted_cb += 1
                    else:
                        total_skipped += 1
                total_inserted += inserted_cb
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Crunchbase: {len(cb_results)} found, {inserted_cb} inserted"))
            except Exception as e:
                logger.error(f"Apify Crunchbase source failed: {e}")
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Crunchbase failed: {e}"))
                
        # 4. Directory Import
        if getattr(config.system.hunter, "use_directories", False):
            try:
                dir_filters = {
                    "sectors": config.targets.targeting.sectors,
                    "limit": 10
                }
                dir_results = await scrape_directories(dir_filters)
                inserted_dir = 0
                for raw in dir_results:
                    if await insert_lead_if_new(session, raw):
                        inserted_dir += 1
                    else:
                        total_skipped += 1
                total_inserted += inserted_dir
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Directories: {len(dir_results)} found, {inserted_dir} inserted"))
            except Exception as e:
                logger.error(f"Apify Directories source failed: {e}")
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Directories failed: {e}"))
            
        # 2. Apify Contact (Backfill missing emails)
        try:
            leads_missing_email = await session.execute(
                select(Lead).where(Lead.status == 'RAW', Lead.email == None, Lead.website != None).limit(10)
            )
            for lead in leads_missing_email.scalars().all():
                try:
                    fast_email = await fast_extract_email(lead.website)
                    if fast_email:
                        lead.email = fast_email
                    else:
                        contacts = await extract_contacts(lead.website)
                        if contacts.get("email"):
                            lead.email = contacts["email"]
                        if contacts.get("phone"):
                            lead.phone = contacts["phone"]
                except Exception as ce:
                    logger.warning(f"Apify Contact failed for {lead.website}: {ce}")
                    
            session.add(ActivityLog(lead_id=None, actor="hunter", action="Hunter - Apify Contact backfill pass completed"))
        except Exception as e:
            logger.error(f"Apify Contact backfill failed: {e}")
            session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Apify Contact backfill failed: {e}"))

        # 3. Apify Lead Scraper
        if config.system.hunter.use_paid_leadscraper:
            try:
                ls_config = {
                    "leadscraper_actor_id": config.system.hunter.leadscraper_actor_id,
                    "leadscraper_input": {}
                }
                ls_results = await scrape_leads(ls_config)
                inserted_ls = 0
                for raw in ls_results:
                    if await insert_lead_if_new(session, raw):
                        inserted_ls += 1
                    else:
                        total_skipped += 1
                total_inserted += inserted_ls
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Lead Scraper: {len(ls_results)} found, {inserted_ls} inserted"))
            except Exception as e:
                logger.error(f"Apify Lead Scraper failed: {e}")
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Lead Scraper failed: {e}"))

        # 4. Local Search (Fallback / Free Alternative)
        if True: # Always attempt fallback if we need leads
            try:
                local_filters = {
                    "sectors": config.targets.targeting.sectors,
                    "location": config.targets.targeting.locations[0] if config.targets.targeting.locations else "Global",
                    "limit": 20
                }
                local_results = await scrape_local_search(local_filters)
                inserted_local = 0
                for raw in local_results:
                    if await insert_lead_if_new(session, raw):
                        inserted_local += 1
                    else:
                        total_skipped += 1
                total_inserted += inserted_local
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Local Search: {len(local_results)} found, {inserted_local} inserted"))
            except Exception as e:
                logger.error(f"Local Search fallback failed: {e}")
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Local Search failed: {e}"))

        # 5. Manual CSV
        if csv_path:
            try:
                csv_res = await import_from_csv(csv_path, session)
                total_inserted += csv_res["inserted"]
                total_skipped += csv_res["skipped_duplicates"]
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Manual CSV: {csv_res['rows_read']} read, {csv_res['inserted']} inserted"))
            except Exception as e:
                logger.error(f"Manual CSV import failed: {e}")
                session.add(ActivityLog(lead_id=None, actor="hunter", action=f"Hunter - Manual CSV failed: {e}"))

        # Update run stats
        if run_id:
            await session.execute(
                update(Run).where(Run.id == run_id).values(
                    leads_scraped=Run.leads_scraped + total_inserted
                )
            )

        await session.commit()
    
    logger.info(f"Hunter module finished: {total_inserted} inserted, {total_skipped} skipped.")
    
    return {
        **state,
        "hunter_inserted": total_inserted,
        "hunter_skipped": total_skipped
    }
