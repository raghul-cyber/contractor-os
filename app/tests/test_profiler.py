import pytest
import pytest_asyncio
import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from app.core.models import Base, Lead, ActivityLog, Run
from app.modules.profiler.synthesizer import ProfileModel, synthesize_profile
from app.modules.profiler.fit_scorer import score_fit
from app.core.llm_router import LLMRouter, RouterConfig
import app.modules.profiler.run as profiler_run_mod

class MockConfig:
    class Targets:
        class Targeting:
            pain_signals = ["slow", "expensive", "outdated"]
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

# 2. Score Fit Tests
def test_score_fit_low_fit(mock_targets_cfg):
    profile = ProfileModel(
        company_name="Test",
        tech_stack=[],
        recent_news="",
        pain_points=["fast", "cheap", "modern"], # No overlap with targets
        personalization_hooks=["hook"]
    )
    score = score_fit(profile, mock_targets_cfg.targets)
    assert score < 0.3 # Should be 0.0

def test_score_fit_high_fit(mock_targets_cfg):
    profile = ProfileModel(
        company_name="Test",
        tech_stack=[],
        recent_news="",
        pain_points=["their process is slow and expensive"], # Overlaps "slow" and "expensive"
        personalization_hooks=["hook"]
    )
    score = score_fit(profile, mock_targets_cfg.targets)
    assert score >= 0.3 # 2 matches * 0.34 = 0.68

# 3. Synthesizer Retry Tests
class FailingMockRouter:
    def __init__(self, fail_times=2):
        self.calls = 0
        self.fail_times = fail_times

    async def call(self, prompt, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            return "This is not JSON! Oops."
        # After failing, return valid JSON
        return """
        ```json
        {
            "company_name": "Test",
            "website": "test.com",
            "industry": "test",
            "size": "10-50",
            "location": "test",
            "tech_stack": [],
            "recent_news": "",
            "pain_points": [],
            "decision_maker": null,
            "decision_maker_email": null,
            "decision_maker_title": null,
            "personalization_hooks": []
        }
        ```
        """

@pytest.mark.asyncio
async def test_synthesizer_retry_success():
    class DummyLead:
        company_name = "Test"
        domain = "test.com"
        website = None
        location = None
        industry = None
        size_range = None
        email = None
        phone = None

    router = FailingMockRouter(fail_times=1)
    
    # 1 fail, then success
    profile = await synthesize_profile(DummyLead(), {}, {}, [], router)
    assert isinstance(profile, ProfileModel)
    assert router.calls == 2

@pytest.mark.asyncio
async def test_synthesizer_retry_failure():
    class DummyLead:
        company_name = "Test"
        domain = "test.com"
        website = None
        location = None
        industry = None
        size_range = None
        email = None
        phone = None

    router = FailingMockRouter(fail_times=3) # Will fail twice
    
    with pytest.raises(ValueError) as excinfo:
        await synthesize_profile(DummyLead(), {}, {}, [], router)
    
    assert "This is not JSON!" in str(excinfo.value)
    assert router.calls == 2 # 1 initial + 1 retry

# 4. Blocked Scrapers Resilience
@pytest_asyncio.fixture
async def temp_db_session_with_lead(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
        lead = Lead(company_name="Test", domain="test.com", source="manual", status="RAW")
        session.add(lead)
        await session.commit()
        
        yield session, lead.id

@pytest.mark.asyncio
async def test_profiler_resilience_to_scraper_failures(temp_db_session_with_lead, monkeypatch):
    session, lead_id = temp_db_session_with_lead
    
    # Mock scrapers to FAIL or return empty
    async def mock_scrape_website(*args, **kwargs):
        raise ValueError("Simulated Website Scrape Failure")
    monkeypatch.setattr(profiler_run_mod, "scrape_website", mock_scrape_website)
    
    async def mock_scrape_linkedin(*args, **kwargs):
        return None # Simulated block
    monkeypatch.setattr(profiler_run_mod, "scrape_linkedin_company", mock_scrape_linkedin)
    
    async def mock_scrape_news(*args, **kwargs):
        return []
    monkeypatch.setattr(profiler_run_mod, "scrape_google_news", mock_scrape_news)
    
    # Mock Config
    monkeypatch.setattr(profiler_run_mod, "get_config", lambda: MockConfig())
    
    # Mock Router to succeed synthesizing from empty data
    class SuccessRouter:
        async def call(self, prompt, **kwargs):
            return """{"company_name": "Test", "website": "test.com", "industry": null, "size": null, "location": null, "tech_stack": [], "recent_news": "", "pain_points": [], "decision_maker": null, "decision_maker_email": null, "decision_maker_title": null, "personalization_hooks": []}"""
    monkeypatch.setattr(profiler_run_mod, "LLMRouter", lambda cfg: SuccessRouter())
    
    # Run
    state = {"run_id": None}
    await profiler_run_mod.run_profiler(state)
    
    # Assert
    lead_res = await session.execute(select(Lead).where(Lead.id == lead_id))
    db_lead = lead_res.scalars().first()
    await session.refresh(db_lead)
    
    # Lead should not be RAW anymore. It should be LOW_FIT because we scored it 0.0 (no pain points)
    assert db_lead.status == "LOW_FIT"
    assert db_lead.profile_json is not None
    assert '"company_name":"Test"' in db_lead.profile_json.replace(" ", "")
