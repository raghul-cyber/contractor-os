import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.core.models import Base, Lead, NegotiatorDraft, EmailEvent
from app.modules.crm.negotiator import draft_reply
import app.modules.crm.negotiator as negotiator_mod

client = TestClient(app)

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import app.api.routes_leads as routes_leads_mod

@pytest_asyncio.fixture
async def db_session(monkeypatch):
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


    monkeypatch.setattr(routes_leads_mod, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        yield session

@pytest.fixture
def mock_router():
    class MockRouter:
        def __init__(self, *args, **kwargs):
            pass
        async def call(self, prompt, **kwargs):
            return """```json
{
  "draft_subject": "Re: Pricing",
  "draft_body": "Here is our pricing: $1500 for the full package.",
  "suggested_next_pipeline_stage": "NEGOTIATING",
  "requires_human_confirmation": false
}
```"""
    return MockRouter()

@pytest.mark.asyncio
async def test_draft_reply_logic(db_session, mock_router):
    # Setup Lead
    lead = Lead(company_name="Test Co", domain="testco.com", status="REPLIED", source="test")
    db_session.add(lead)
    await db_session.flush()
    
    # Run draft_reply
    result = await draft_reply(lead.id, "How much does it cost?", db_session, mock_router)
    
    # Assert return value
    assert result["draft_subject"] == "Re: Pricing"
    # Even though mock returned false, our programmatic regex should flip it to True!
    assert result["requires_human_confirmation"] is True
    
    # Assert DB state
    res = await db_session.execute(select(NegotiatorDraft).where(NegotiatorDraft.lead_id == lead.id))
    draft = res.scalar_one()
    assert draft.draft_subject == "Re: Pricing"
    assert draft.requires_human_confirmation is True

@pytest.mark.asyncio
async def test_negotiator_api_draft_and_send(db_session, mock_router, monkeypatch):
    # Setup
    lead = Lead(company_name="API Test", domain="api.com", status="REPLIED", source="test", email="api@test.com")
    db_session.add(lead)
    await db_session.flush()
    await db_session.commit()
    
    # Mock LLMRouter.call
    import app.core.llm_router as llm_router_mod
    monkeypatch.setattr(llm_router_mod.LLMRouter, "call", mock_router.call)
    
    # 1. Test /draft
    # Check initial draft row count
    res_before = await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    assert len(res_before.scalars().all()) == 0
    
    res = client.post(f"/api/leads/{lead.id}/negotiator/draft", json={"incoming_message_text": "hello"})
    assert res.status_code == 200
    data = res.json()
    assert data["draft_subject"] == "Re: Pricing"
    
    # Draft should not create EmailEvents!
    res_after = await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    assert len(res_after.scalars().all()) == 0
    
    # 2. Test /send honoring dry_run
    class FakeIdentity:
        email = "test_identity@ahixlight.com"
        daily_send_limit = 20
    class FakeOutreachConfig:
        sending_identities = [FakeIdentity()]
        dry_run = True # global dry run
    class FakeSystem:
        outreach = FakeOutreachConfig()
    class FakeConfig:
        outreach = FakeOutreachConfig()
        system = FakeSystem()
        
    import app.core.config_loader as config_loader_mod
    import app.modules.outreach.sender as sender_mod
    monkeypatch.setattr(config_loader_mod, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(sender_mod, "get_config", lambda: FakeConfig())
    # Note: We do NOT mock send_email. It should internally respect system.outreach.dry_run = True
    # and return gracefully without raising an exception from missing SMTP creds.
    
    send_res = client.post(f"/api/leads/{lead.id}/negotiator/send", json={"subject": "S", "body": "B"})
    assert send_res.status_code == 200
    assert send_res.json()["status"] == "success"
    
    # Verify EmailEvent logged because in ContractorOS, dry_run=True still triggers the DB state transition
    events_res = await db_session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    events = events_res.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "sent"
    assert events[0].sending_identity == "test_identity@ahixlight.com"
