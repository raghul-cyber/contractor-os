import pytest
import pytest_asyncio
import json
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, text
from app.core.models import Base, Lead, ActivityLog, OutreachSequence
import app.modules.craft.run as craft_run_mod

class MockProfile:
    name = "Test Contractor"
    description = "Test Desc"
    price_range = "$1k"
    value_proposition = "Test Value"
    class MockService:
        def __init__(self, name, description, price_range):
            self.name = name
            self.description = description
            self.price_range = price_range
            self.category = "Test Category"
            
    services = [
        MockService("S1", "D1", "$1k"),
        MockService("S2", "D2", "$2k")
    ]

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
async def temp_db_session_craft(monkeypatch):
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
        # 1 Valid Lead
        profile_json = json.dumps({"personalization_hooks": ["Specific Hook Here"]})
        l1 = Lead(company_name="Valid", domain="1.com", status="RESEARCHED", source="test", profile_json=profile_json)
        # 1 Failing Lead
        l2 = Lead(company_name="Failed", domain="2.com", status="RESEARCHED", source="test", profile_json="{}")
        
        session.add_all([l1, l2])
        await session.commit()
        
        yield session, l1.id, l2.id

class SpyRouter:
    def __init__(self):
        self.calls = 0
    
    async def call(self, prompt, **kwargs):
        self.calls += 1
        if "Valid" in prompt:
            if self.calls == 1:
                # Over limits to test truncation
                return json.dumps({
                    "initial": {"subject": "Subj 1", "body": "word " * 500},
                    "fu1": {"subject": "Subj 2", "body": "Specific Hook Here " + "word " * 300},
                    "fu2": {"subject": "Subj 3", "body": "Specific Hook Here " + "word " * 300},
                    "fu3": {"subject": "Subj 4", "body": "Specific Hook Here " + "word " * 300}
                })
        else:
            return "totally invalid json"

@pytest.mark.asyncio
async def test_craft_run_full(temp_db_session_craft, monkeypatch):
    session, l1_id, l2_id = temp_db_session_craft
    
    monkeypatch.setattr(craft_run_mod, "get_config", lambda: MockConfig())
    spy_router = SpyRouter()
    monkeypatch.setattr(craft_run_mod, "LLMRouter", lambda cfg: spy_router)
    
    # We must patch the prompts_dir path so it finds the actual txt files we wrote,
    # or just create a temporary directory or mock open. 
    # Since we are running in the repo root, it should find them inherently via __file__.
    
    res = await craft_run_mod.run_craft({})
    
    # 2 leads processed, 1 successful
    assert res["craft_processed"] == 2
    assert res["craft_successful"] == 1
    
    # Assert Router calls: exactly 1 per lead (no retries coded for craft, it just fails to CRAFT_FAILED)
    assert spy_router.calls == 2
    
    # Check Leads
    leads_res = await session.execute(select(Lead).order_by(Lead.id))
    leads = leads_res.scalars().all()
    await session.refresh(leads[0])
    await session.refresh(leads[1])
    
    assert leads[0].status == "DRAFTED"
    assert leads[1].status == "CRAFT_FAILED"
    
    # Check OutreachSequences for l1
    seqs_res = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == l1_id))
    seqs = seqs_res.scalars().all()
    
    assert len(seqs) == 4
    for seq in seqs:
        assert seq.status == "draft" # Auto-approval respected
        
        # Word counts enforced programmatically
        words = len(seq.body.split())
        if seq.sequence_type == "initial":
            assert words <= 405 # 400 + " ... [TRUNCATED]" is ~ 403
            assert "[TRUNCATED]" in seq.body
        else:
            assert words <= 205
            assert "Specific Hook Here" in seq.body
