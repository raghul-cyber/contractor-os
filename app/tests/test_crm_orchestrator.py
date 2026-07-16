import pytest
import pytest_asyncio
import asyncio
from datetime import datetime
import os

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from app.core.models import Base, Lead, Run, Pipeline, ActivityLog, OutreachSequence, EmailEvent
import app.modules.orchestrator.graph as graph_mod
import app.modules.crm.transitions as trans_mod
import app.modules.crm.digest as digest_mod
import app.modules.crm.pipeline_api as pipe_mod

class MockConfig:
    class System:
        batch_size = 5
        cycle_interval_hours = 6
        class Craft:
            require_manual_approval = True
        craft = Craft()
        class Outreach:
            dry_run = True
            daily_send_limit = 20
            follow_up_intervals_days = [5, 10, 15]
            send_backend = "smtp"
        outreach = Outreach()
    system = System()

@pytest_asyncio.fixture
async def temp_db_session_orch(monkeypatch):
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

    monkeypatch.setattr(graph_mod, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        yield session

@pytest.mark.asyncio
async def test_graph_full_cycle(temp_db_session_orch, monkeypatch):
    session = temp_db_session_orch
    monkeypatch.setattr(graph_mod, "get_config", lambda: MockConfig())
    
    # Mock node implementations
    async def mock_hunter(state): return state
    async def mock_profiler(state): return state
    async def mock_craft(state): return state
    async def mock_outreach(state): return state
    
    monkeypatch.setattr(graph_mod, "run_hunter", mock_hunter)
    monkeypatch.setattr(graph_mod, "run_profiler", mock_profiler)
    monkeypatch.setattr(graph_mod, "run_craft", mock_craft)
    monkeypatch.setattr(graph_mod, "run_outreach", mock_outreach)
    
    await graph_mod.run_full_cycle()
    
    runs = await session.execute(select(Run))
    run = runs.scalar_one_or_none()
    assert run is not None
    assert run.completed_at is not None
    assert run.errors == 0

@pytest.mark.asyncio
async def test_graph_node_failure(temp_db_session_orch, monkeypatch):
    session = temp_db_session_orch
    monkeypatch.setattr(graph_mod, "get_config", lambda: MockConfig())
    
    lead = Lead(company_name="T", domain="t.com", status="RAW", source="test")
    session.add(lead)
    await session.commit()
    
    async def mock_hunter(state):
        state["lead_ids"] = [lead.id]
        return state
        
    async def mock_profiler(state):
        raise ValueError("Simulated unrecoverable failure")
        
    async def mock_craft(state): return state
    async def mock_outreach(state): return state
    
    monkeypatch.setattr(graph_mod, "run_hunter", mock_hunter)
    monkeypatch.setattr(graph_mod, "run_profiler", mock_profiler)
    monkeypatch.setattr(graph_mod, "run_craft", mock_craft)
    monkeypatch.setattr(graph_mod, "run_outreach", mock_outreach)
    
    await graph_mod.run_full_cycle()
    
    await session.refresh(lead)
    assert lead.status == "PROFILE_FAILED"
    
    logs = await session.execute(select(ActivityLog).where(ActivityLog.lead_id == lead.id))
    log = logs.scalars().first()
    assert log is not None
    assert log.actor == "orchestrator"
    assert "Simulated unrecoverable failure" in log.detail
    
    runs = await session.execute(select(Run))
    run = runs.scalar_one_or_none()
    assert run.errors == 1
    assert run.completed_at is not None

@pytest.mark.asyncio
async def test_crm_mark_replied_ambiguous(temp_db_session_orch, monkeypatch):
    session = temp_db_session_orch
    monkeypatch.setattr(trans_mod, "get_config", lambda: MockConfig())
    
    lead = Lead(company_name="R", domain="r.com", status="SENT", source="test")
    session.add(lead)
    await session.commit()
    
    session.add(OutreachSequence(lead_id=lead.id, sequence_type="fu1", subject="S", body="B", status="queued"))
    await session.commit()
    
    class MockRouter:
        async def call(self, prompt, **kwargs):
            return "polite-no"
            
    monkeypatch.setattr(trans_mod, "LLMRouter", lambda cfg: MockRouter())
    
    # Do not call real webhook
    async def mock_notify(msg): pass
    monkeypatch.setattr(trans_mod, "notify_webhook", mock_notify)
    
    await trans_mod.mark_replied(lead.id, "neutral", "please stop emailing me", session)
    await session.commit()
    
    await session.refresh(lead)
    assert lead.status == "REPLIED"
    
    pipe = await session.execute(select(Pipeline).where(Pipeline.lead_id == lead.id))
    p = pipe.scalar_one()
    assert p.stage == "LOST" # Due to polite-no classification
    
    seqs = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == lead.id))
    assert seqs.scalars().first().status == "cancelled"

@pytest.mark.asyncio
async def test_daily_digest(temp_db_session_orch, monkeypatch):
    session = temp_db_session_orch
    async def mock_notify(msg): pass
    monkeypatch.setattr(digest_mod, "notify_webhook", mock_notify)
    
    run1 = Run(leads_scraped=5, leads_researched=3, emails_sent=10, replies_received=2)
    session.add(run1)
    
    # 1 hot lead, 1 active deal
    pipe1 = Pipeline(lead_id=1, stage="REPLIED", contract_value=500.0) # Hot & Active
    pipe2 = Pipeline(lead_id=2, stage="WON", contract_value=1000.0) # Not active
    session.add_all([pipe1, pipe2])
    
    await session.commit()
    
    digest_text = await digest_mod.run_daily_digest(session)
    
    assert "Leads Scraped:      5" in digest_text
    assert "Profiles Generated: 3" in digest_text
    assert "Emails Sent:        10" in digest_text
    assert "Replies Received:   2" in digest_text
    assert "Hot Leads:          1" in digest_text
    assert "Active Deals:       1" in digest_text
    assert "Pipeline Value:     $500.00" in digest_text
    
    # File check
    today_str = datetime.utcnow().date().isoformat()
    assert os.path.exists(f"logs/digest_{today_str}.txt")
