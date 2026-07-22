import pytest
import pytest_asyncio
import json
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, text
from app.core.models import Base, Lead, ActivityLog, Run
from app.modules.profiler.synthesizer import ProfileModel, synthesize_profile
from app.modules.profiler.fit_scorer import score_fit
from app.core.llm_router import LLMRouter, RouterConfig
import app.modules.profiler.run as profiler_run_mod
from app.modules.profiler.scrapers.website import scrape_website
from app.modules.profiler.scrapers.linkedin_company import scrape_linkedin_company
from app.modules.profiler.scrapers.news import scrape_google_news

class MockConfig:
    class Targets:
        class Targeting:
            pain_signals = ["no dedicated security team", "hiring DevOps", "scaling engineering team"]
        targeting = Targeting()
    class System:
        class Profiler:
            min_fit_score = 0.3
            concurrent_scrapes = 5
        profiler = Profiler()
        batch_size = 5
    targets = Targets()
    system = System()

@pytest.fixture
def mock_targets_cfg():
    return MockConfig()

# ----------------- 1. SCORE FIT TESTS -----------------

def test_score_fit(mock_targets_cfg):
    # High fit
    profile_high = ProfileModel(
        company_name="Test",
        tech_stack=[],
        recent_news="",
        pain_points=["they have no dedicated security team and are hiring DevOps"], # 2 matches
        personalization_hooks=["hook"]
    )
    score_high = score_fit(profile_high, mock_targets_cfg.targets)
    
    # Low fit
    profile_low = ProfileModel(
        company_name="Test",
        tech_stack=[],
        recent_news="",
        pain_points=["fast", "cheap"], # 0 matches
        personalization_hooks=["modern"]
    )
    score_low = score_fit(profile_low, mock_targets_cfg.targets)
    
    assert score_high > score_low
    assert score_high >= mock_targets_cfg.system.profiler.min_fit_score
    assert score_low < mock_targets_cfg.system.profiler.min_fit_score

# ----------------- 2. SYNTHESIZER TESTS -----------------

class DummyLead:
    def __init__(self, name="Test", id=1):
        self.id = id
        self.company_name = name
        self.domain = "test.com"
        self.website = None
        self.location = None
        self.industry = None
        self.size_range = None
        self.email = None
        self.phone = None

class MockRouterValidAfterRetry:
    def __init__(self):
        self.calls = 0
    async def call(self, prompt, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "This is a prose response that completely fails JSON parsing."
        return '{"company_name": "Test", "website": "test.com", "industry": null, "size": null, "location": null, "tech_stack": [], "recent_news": "", "pain_points": [], "decision_maker": null, "decision_maker_email": null, "decision_maker_title": null, "personalization_hooks": []}'

class MockRouterAlwaysInvalid:
    def __init__(self):
        self.calls = 0
    async def call(self, prompt, **kwargs):
        self.calls += 1
        return "I am an AI and I will not give you JSON."

@pytest.mark.asyncio
async def test_synthesize_profile_retry():
    router = MockRouterValidAfterRetry()
    profile = await synthesize_profile(DummyLead(), {}, {}, [], [], router)
    assert router.calls == 2
    assert isinstance(profile, ProfileModel)

@pytest.mark.asyncio
async def test_synthesize_profile_failure():
    router = MockRouterAlwaysInvalid()
    with pytest.raises(ValueError) as excinfo:
        await synthesize_profile(DummyLead(), {}, {}, [], [], router)
    assert router.calls == 2
    assert "I am an AI" in str(excinfo.value)

# ----------------- 3. SCRAPER INDEPENDENT MOCK TESTS -----------------

@pytest.mark.asyncio
async def test_website_partial_404(monkeypatch):
    import app.modules.profiler.scrapers.website as website_mod

    async def mock_fetch_url(url, use_stealth=False):
        if "/about" in url:
            return "" # Simulating a 404 gracefully handled
        return f"Content of {url}"

    monkeypatch.setattr(website_mod, "_fetch_url", mock_fetch_url)
    
    class MockResponse:
        status = 200
        body = b"Mock homepage content"
        text = "Mock homepage content"
        
    class MockFetcher:
        def get(self, url): return MockResponse()
        
    monkeypatch.setattr(website_mod, "Fetcher", MockFetcher)
    monkeypatch.setattr(website_mod, "_extract_text_from_scrapling", lambda r: "Extracted mock homepage")
    
    res = await website_mod.scrape_website("https://example.com")
    
    assert "homepage" in res
    assert "about" in res
    assert "services" in res
    
    # Even though /about failed, we should still get homepage and services
    assert res["about"] == ""
    assert "Content" in res["services"]
    assert "Extracted mock homepage" in res["homepage"]

@pytest.mark.asyncio
async def test_linkedin_403(monkeypatch):
    import app.modules.profiler.scrapers.linkedin as linkedin_mod
    
    class MockResponse:
        @property
        def ok(self): return False
        @property
        def status(self): return 403
        
    class MockPage:
        async def goto(self, url, **kwargs): return MockResponse()
        
    class MockContext:
        async def new_page(self): return MockPage()
    class MockBrowser:
        async def new_context(self, **kwargs): return MockContext()
        async def close(self): pass
    class MockChromium:
        async def launch(self, **kwargs): return MockBrowser()
    class MockPlaywright:
        @property
        def chromium(self): return MockChromium()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    monkeypatch.setattr(linkedin_mod, "async_playwright", lambda: MockPlaywright())
    
    res = await scrape_linkedin_company("Test Company", "test.com")
    assert res is None # Did not raise

@pytest.mark.asyncio
async def test_news_malformed(monkeypatch):
    import app.modules.profiler.scrapers.news as news_mod
    
    # Mock feedparser to throw exception
    def _mock_parse(url):
        raise Exception("Mocked error")
    monkeypatch.setattr(news_mod.feedparser, "parse", _mock_parse)
    
    res = await scrape_google_news("Test")
    assert res == [] # Graceful

# ----------------- 4. FULL RUN_PROFILER TEST -----------------

@pytest_asyncio.fixture
async def temp_db_session_with_3_leads(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON;")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    
    class MockSessionContext:
        async def __aenter__(self):
            self.session = SessionLocal()
            return self.session
        async def __aexit__(self, exc_type, exc, tb):
            await self.session.close()

    monkeypatch.setattr(profiler_run_mod, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        # Create run
        run = Run(leads_scraped=0, leads_researched=0)
        session.add(run)
        
        # 3 Leads
        l1 = Lead(company_name="Lead_Researched", domain="1.com", status="RAW", source="test")
        l2 = Lead(company_name="Lead_LowFit", domain="2.com", status="RAW", source="test")
        l3 = Lead(company_name="Lead_Failed", domain="3.com", status="RAW", source="test")
        
        session.add_all([l1, l2, l3])
        await session.commit()
        
        yield session, run.id

@pytest.mark.asyncio
async def test_full_run_profiler(temp_db_session_with_3_leads, monkeypatch):
    session, run_id = temp_db_session_with_3_leads
    
    # Mock Scrapers
    async def mock_website(*args, **kwargs): return {}
    async def mock_linkedin(*args, **kwargs): return None
    async def mock_news(*args, **kwargs): return []
    
    monkeypatch.setattr(profiler_run_mod, "scrape_website", mock_website)
    monkeypatch.setattr(profiler_run_mod, "scrape_linkedin_company", mock_linkedin)
    monkeypatch.setattr(profiler_run_mod, "scrape_google_news", mock_news)
    
    # Mock Config
    monkeypatch.setattr(profiler_run_mod, "get_config", lambda: MockConfig())
    
    # Mock Router that routes based on company name
    class SmartMockRouter:
        async def call(self, prompt, **kwargs):
            if "Lead_Researched" in prompt:
                # Provide valid JSON with pain signal overlap
                return """{"company_name": "Lead_Researched", "website": "1.com", "industry": null, "size": null, "location": null, "tech_stack": [], "recent_news": "", "pain_points": ["hiring DevOps"], "decision_maker": null, "decision_maker_email": null, "decision_maker_title": null, "personalization_hooks": []}"""
            elif "Lead_LowFit" in prompt:
                # Provide valid JSON with no overlap
                return """{"company_name": "Lead_LowFit", "website": "2.com", "industry": null, "size": null, "location": null, "tech_stack": [], "recent_news": "", "pain_points": ["fast"], "decision_maker": null, "decision_maker_email": null, "decision_maker_title": null, "personalization_hooks": []}"""
            elif "Lead_Failed" in prompt:
                # Always invalid
                return "Totally invalid JSON"
            return "{}"

    monkeypatch.setattr(profiler_run_mod, "LLMRouter", lambda cfg: SmartMockRouter())
    
    state = {"run_id": run_id}
    res = await profiler_run_mod.run_profiler(state)
    
    # 3 leads processed, 2 returned True (Researched and LowFit), 1 False (Failed)
    assert res["profiler_processed"] == 3
    assert res["profiler_successful"] == 2
    
    # Check Leads
    leads_res = await session.execute(select(Lead).order_by(Lead.id))
    leads = leads_res.scalars().all()
    
    for lead_record in leads:
        await session.refresh(lead_record)
    
    assert leads[0].status == "RESEARCHED"
    assert leads[1].status == "LOW_FIT"
    assert leads[2].status == "PROFILE_FAILED"
    
    # Check Run stats
    run_res = await session.execute(select(Run).where(Run.id == run_id))
    db_run = run_res.scalars().first()
    await session.refresh(db_run)
    assert db_run.leads_researched == 2
    
    # Check ActivityLog for the failed one
    log_res = await session.execute(select(ActivityLog).where(ActivityLog.lead_id == leads[2].id))
    logs = log_res.scalars().all()
    assert any("JSON failed" in log.action for log in logs)
    assert any("Totally invalid JSON" in log.detail for log in logs)
