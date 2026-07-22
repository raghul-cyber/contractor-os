import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from app.core.models import Base, Lead, ActivityLog, Run
import app.modules.hunter.run as run_mod
from app.modules.hunter.run import run_hunter

import pytest_asyncio

@pytest_asyncio.fixture
async def temp_db_session_with_run(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON;")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    
    # Mock get_session in run.py to use our SessionLocal
    class MockSessionContext:
        async def __aenter__(self):
            self.session = SessionLocal()
            return self.session
        async def __aexit__(self, exc_type, exc, tb):
            await self.session.close()

    monkeypatch.setattr(run_mod, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        # Create a Run row to test run stats update
        test_run = Run(leads_scraped=0)
        session.add(test_run)
        await session.commit()
        
        yield session, test_run.id

@pytest.mark.asyncio
async def test_full_run_hunter(temp_db_session_with_run, monkeypatch):
    session, run_id = temp_db_session_with_run
    
    # 1. Mock Apify Maps to SUCCEED
    async def mock_scrape_google_maps(filters, *args, **kwargs):
        return [{"company_name": "MapLead", "website": "maplead.com"}]
    monkeypatch.setattr(run_mod, "scrape_google_maps", mock_scrape_google_maps)
    
    # 2. Mock Apify Contact to return a value
    async def mock_extract_contacts(url, *args, **kwargs):
        return {"email": "hello@maplead.com"}
    monkeypatch.setattr(run_mod, "extract_contacts", mock_extract_contacts)
    
    # 3. Mock Apify Leadscraper to FAIL completely
    async def mock_scrape_leads(config, *args, **kwargs):
        raise ValueError("Simulated Leadscraper Failure")
    monkeypatch.setattr(run_mod, "scrape_leads", mock_scrape_leads)
    
    # 4. Mock Config to force Leadscraper to run
    class MockConfig:
        class Targets:
            class Targeting:
                sectors = ["Test"]
                locations = ["Test"]
            targeting = Targeting()
        class System:
            class Hunter:
                use_paid_leadscraper = True
                leadscraper_actor_id = "test"
            hunter = Hunter()
        targets = Targets()
        system = System()
        
    monkeypatch.setattr(run_mod, "get_config", lambda: MockConfig())
    
    # Run
    state = {"run_id": run_id, "csv_path": None}
    new_state = await run_hunter(state)
    
    # Assert state output
    assert new_state["hunter_inserted"] == 1
    assert new_state["hunter_skipped"] == 0
    
    # Assert DB State
    leads_res = await session.execute(select(Lead))
    leads = leads_res.scalars().all()
    assert len(leads) == 1
    assert leads[0].domain == "maplead.com"
    # Contact backfill should have filled the email
    assert leads[0].email == "hello@maplead.com"
    
    # Assert Activity Log has expected entries
    logs_res = await session.execute(select(ActivityLog).where(ActivityLog.actor == "hunter"))
    logs = logs_res.scalars().all()
    
    actions = [log.action for log in logs]
    assert any("Hunter - Apify Maps: 1 found, 1 inserted" in a for a in actions)
    assert any("Hunter - Apify Contact backfill pass completed" in a for a in actions)
    assert any("Hunter - Lead Scraper failed: Simulated Leadscraper Failure" in a for a in actions)
    
    # Assert Run leads_scraped counter was incremented
    run_res = await session.execute(select(Run).where(Run.id == run_id))
    db_run = run_res.scalars().first()
    await session.refresh(db_run)
    assert db_run.leads_scraped == 1

@pytest.mark.asyncio
async def test_hunter_toggles(temp_db_session_with_run, monkeypatch):
    session, run_id = temp_db_session_with_run
    
    async def mock_scrape_google_maps(*args, **kwargs): return [{"company_name": "Map", "domain": "map.com"}]
    async def mock_scrape_job_boards(*args, **kwargs): return [{"company_name": "Job", "domain": "job.com"}]
    async def mock_scrape_crunchbase(*args, **kwargs): return [{"company_name": "CB", "domain": "cb.com"}]
    async def mock_scrape_directories(*args, **kwargs): return [{"company_name": "Dir", "domain": "dir.com"}]
    async def mock_extract_contacts(*args, **kwargs): return {}
    
    monkeypatch.setattr(run_mod, "scrape_google_maps", mock_scrape_google_maps)
    monkeypatch.setattr(run_mod, "scrape_job_boards", mock_scrape_job_boards)
    monkeypatch.setattr(run_mod, "scrape_crunchbase", mock_scrape_crunchbase)
    monkeypatch.setattr(run_mod, "scrape_directories", mock_scrape_directories)
    monkeypatch.setattr(run_mod, "extract_contacts", mock_extract_contacts)
    
    class MockConfig:
        class Targets:
            class Targeting:
                sectors = ["Test"]
                locations = ["Test"]
                pain_signals = ["Test"]
            targeting = Targeting()
        class System:
            class Hunter:
                use_paid_leadscraper = False
                use_jobboard_signals = False
                use_crunchbase = False
                use_directories = False
            hunter = Hunter()
        targets = Targets()
        system = System()
        
    config = MockConfig()
    monkeypatch.setattr(run_mod, "get_config", lambda: config)
    
    # 1. Enable ONLY job boards, maps is enabled by default in the script?
    # Wait, maps is NOT toggleable in run.py! It's always run.
    # To test toggleable, we can just check what gets inserted when we flip a flag.
    # We will test Job Boards.
    # Actually, maps is always run in run.py. We can't toggle it off without changing code.
    # We will just verify that Job is NOT inserted when false, but IS inserted when true.
    
    state = {"run_id": run_id, "csv_path": None}
    
    # Run 1: all new sources false
    await run_hunter(state)
    res = await session.execute(select(Lead))
    leads = [l.company_name for l in res.scalars().all()]
    assert "Map" in leads
    assert "Job" not in leads
    assert "CB" not in leads
    assert "Dir" not in leads
    
    # Run 2: turn on Job Boards
    config.system.hunter.use_jobboard_signals = True
    await run_hunter(state)
    res = await session.execute(select(Lead))
    leads = [l.company_name for l in res.scalars().all()]
    assert "Job" in leads
    assert "CB" not in leads
    assert "Dir" not in leads
    
    # Run 3: turn on Crunchbase
    config.system.hunter.use_crunchbase = True
    await run_hunter(state)
    res = await session.execute(select(Lead))
    leads = [l.company_name for l in res.scalars().all()]
    assert "CB" in leads
    
    # Run 4: turn on Directories
    config.system.hunter.use_directories = True
    await run_hunter(state)
    res = await session.execute(select(Lead))
    leads = [l.company_name for l in res.scalars().all()]
    assert "Dir" in leads
