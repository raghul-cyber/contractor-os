import pytest
import pytest_asyncio
import json
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, text
from app.core.models import Base, Lead, ActivityLog, OutreachSequence
import app.modules.craft.run as craft_run_mod

class MockService:
    def __init__(self, name="Test Contractor", description="Test Desc", price_range="$1k"):
        self.name = name
        self.description = description
        self.price_range = price_range
        self.category = "Test Category"

class MockProfile:
    services = [MockService()]
    value_proposition = "Test Value"

class MockConfig:
    class System:
        batch_size = 5
        class Craft:
            require_manual_approval = True
        craft = Craft()
    profile = MockProfile()
    class CatalogMock:
        services = MockProfile.services
    catalog = CatalogMock()
    system = System()

@pytest_asyncio.fixture
async def temp_db_session_craft_prompts(monkeypatch):
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

    monkeypatch.setattr(craft_run_mod, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        # Seed RESEARCHED lead
        profile_json = json.dumps({"personalization_hooks": ["Raised $4M Series A in April 2026"]})
        l1 = Lead(company_name="Valid", domain="1.com", status="RESEARCHED", source="test", profile_json=profile_json)
        
        # Seed Exclusions
        l2 = Lead(company_name="LowFit", domain="2.com", status="LOW_FIT", source="test")
        l3 = Lead(company_name="ProfileFailed", domain="3.com", status="PROFILE_FAILED", source="test")
        
        session.add_all([l1, l2, l3])
        await session.commit()
        
        yield session, l1.id, l2.id, l3.id

class SpyRouterForPrompts:
    def __init__(self, over_word_limit=False):
        self.calls = 0
        self.over_word_limit = over_word_limit
        self.last_prompt = ""
    
    async def call(self, prompt, **kwargs):
        self.calls += 1
        self.last_prompt = prompt
        
        if self.over_word_limit:
            initial_body = "word " * 200
        else:
            initial_body = "normal valid length body"
            
        return json.dumps({
            "initial": {"subject": "Subj 1", "body": initial_body},
            "fu1": {"subject": "Subj 2", "body": "short fu"},
            "fu2": {"subject": "Subj 3", "body": "short fu"},
            "fu3": {"subject": "Subj 4", "body": "short fu"}
        })

@pytest.mark.asyncio
async def test_craft_valid_4_emails(temp_db_session_craft_prompts, monkeypatch):
    session, l1_id, l2_id, l3_id = temp_db_session_craft_prompts
    monkeypatch.setattr(craft_run_mod, "get_config", lambda: MockConfig())
    
    spy_router = SpyRouterForPrompts()
    monkeypatch.setattr(craft_run_mod, "LLMRouter", lambda cfg: spy_router)
    
    await craft_run_mod.run_craft({})
    
    # Assert single router call (only 1 RESEARCHED lead)
    assert spy_router.calls == 1
    
    # Assert DRAFTED
    leads_res = await session.execute(select(Lead).where(Lead.id == l1_id))
    l1 = leads_res.scalars().first()
    await session.refresh(l1)
    assert l1.status == "DRAFTED"
    
    # Assert 4 sequences, all draft
    seqs_res = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == l1_id))
    seqs = seqs_res.scalars().all()
    assert len(seqs) == 4
    for seq in seqs:
        assert seq.status == "draft"

@pytest.mark.asyncio
async def test_craft_word_count_enforcement(temp_db_session_craft_prompts, monkeypatch):
    session, l1_id, l2_id, l3_id = temp_db_session_craft_prompts
    monkeypatch.setattr(craft_run_mod, "get_config", lambda: MockConfig())
    
    spy_router = SpyRouterForPrompts(over_word_limit=True)
    monkeypatch.setattr(craft_run_mod, "LLMRouter", lambda cfg: spy_router)
    
    await craft_run_mod.run_craft({})
    
    # Check the initial sequence word count
    seqs_res = await session.execute(
        select(OutreachSequence).where(OutreachSequence.lead_id == l1_id, OutreachSequence.sequence_type == "initial")
    )
    initial_seq = seqs_res.scalars().first()
    
    # 150 words + 3 words for "... [TRUNCATED]"
    assert len(initial_seq.body.split()) <= 153 
    assert "[TRUNCATED]" in initial_seq.body

@pytest.mark.asyncio
async def test_craft_personalization_hook_in_prompt(temp_db_session_craft_prompts, monkeypatch):
    session, l1_id, l2_id, l3_id = temp_db_session_craft_prompts
    monkeypatch.setattr(craft_run_mod, "get_config", lambda: MockConfig())
    
    spy_router = SpyRouterForPrompts()
    monkeypatch.setattr(craft_run_mod, "LLMRouter", lambda cfg: spy_router)
    
    await craft_run_mod.run_craft({})
    
    # Assert the exact specific hook passed to the prompt
    assert "Raised $4M Series A in April 2026" in spy_router.last_prompt

@pytest.mark.asyncio
async def test_craft_approval_gate(temp_db_session_craft_prompts, monkeypatch):
    session, l1_id, l2_id, l3_id = temp_db_session_craft_prompts
    monkeypatch.setattr(craft_run_mod, "get_config", lambda: MockConfig())
    
    spy_router = SpyRouterForPrompts()
    monkeypatch.setattr(craft_run_mod, "LLMRouter", lambda cfg: spy_router)
    
    await craft_run_mod.run_craft({})
    
    # Assert zero rows have status='approved'
    seqs_res = await session.execute(select(OutreachSequence).where(OutreachSequence.status == "approved"))
    approved_seqs = seqs_res.scalars().all()
    assert len(approved_seqs) == 0

@pytest.mark.asyncio
async def test_craft_exclusion(temp_db_session_craft_prompts, monkeypatch):
    session, l1_id, l2_id, l3_id = temp_db_session_craft_prompts
    monkeypatch.setattr(craft_run_mod, "get_config", lambda: MockConfig())
    
    spy_router = SpyRouterForPrompts()
    monkeypatch.setattr(craft_run_mod, "LLMRouter", lambda cfg: spy_router)
    
    await craft_run_mod.run_craft({})
    
    # Assert l2 and l3 produced ZERO sequences
    seqs_l2_res = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == l2_id))
    assert len(seqs_l2_res.scalars().all()) == 0
    
    seqs_l3_res = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == l3_id))
    assert len(seqs_l3_res.scalars().all()) == 0
