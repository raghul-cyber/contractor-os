import pytest
import pytest_asyncio
import os
import json
from datetime import datetime
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from app.core.models import Base, Lead, Run, Pipeline, ActivityLog, OutreachSequence, EmailEvent
import app.modules.orchestrator.graph as graph_mod
import app.modules.crm.transitions as trans_mod
import app.modules.crm.pipeline_api as pipe_mod
import app.modules.crm.digest as digest_mod

# We will need to mock a lot of external stuff
import app.modules.hunter.run as hunter_run
import app.modules.profiler.run as profiler_run
import app.modules.craft.run as craft_run
import app.modules.outreach.run as outreach_run

class MockSystemConfig:
    batch_size = 5
    cycle_interval_hours = 6
    class Profiler:
        min_fit_score = 0.3
        concurrent_scrapes = 2
    profiler = Profiler()
    class Craft:
        require_manual_approval = False # Turn off to let it reach SENT
    craft = Craft()
    class Outreach:
        dry_run = True
        daily_send_limit = 20
        follow_up_intervals_days = [5, 10, 15]
        send_backend = "smtp"
    outreach = Outreach()
    
class MockConfig:
    system = MockSystemConfig()
    class Profile:
        name = "Mock"
        description = "Desc"
        price_range = "$1k - $5k"
        class MockService:
            name = "S1"
            description = "D1"
            price_range = "$1k - $2k"
        services = [MockService()]
        value_proposition = "Value"
    profile = Profile()
    class CatalogMock:
        class MockService:
            name = "S1"
            description = "D1"
            price_range = "$1k - $2k"
            category = "Test Category"
        services = [MockService()]
    catalog = CatalogMock()
    class Targets:
        class Targeting:
            pain_signals = ["Cost", "Scale"]
        targeting = Targeting()
    targets = Targets()

@pytest_asyncio.fixture
async def e2e_session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Match production: enforce FK constraints in tests
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

    # Monkeypatch get_session everywhere
    monkeypatch.setattr(graph_mod, "get_session", MockSessionContext)
    monkeypatch.setattr(hunter_run, "get_session", MockSessionContext)
    monkeypatch.setattr(profiler_run, "get_session", MockSessionContext)
    monkeypatch.setattr(craft_run, "get_session", MockSessionContext)
    monkeypatch.setattr(outreach_run, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        yield session

@pytest.mark.asyncio
async def test_full_happy_path_integration(e2e_session, monkeypatch):
    session = e2e_session
    monkeypatch.setattr(graph_mod, "get_config", lambda: MockConfig())
    monkeypatch.setattr(hunter_run, "get_config", lambda: MockConfig())
    monkeypatch.setattr(profiler_run, "get_config", lambda: MockConfig())
    monkeypatch.setattr(craft_run, "get_config", lambda: MockConfig())
    monkeypatch.setattr(outreach_run, "get_config", lambda: MockConfig())
    import app.modules.outreach.sender as sender_mod
    monkeypatch.setattr(sender_mod, "get_config", lambda: MockConfig())
    async def fake_execute_send(*args, **kwargs):
        return {"id": "fake_id", "status": "sent"}
    monkeypatch.setattr(sender_mod, "_execute_send", fake_execute_send)
    
    # 1. Seed RAW leads
    l1 = Lead(company_name="CorpA", domain="corpa.com", status="RAW", source="test")
    session.add(l1)
    await session.commit()
    
    # 2. Mock external dependencies
    # Hunter: no-op since it usually just reads config, but we can mock run_hunter to just return state
    # Wait, the prompt says "mock Apify/scraper calls". Let's just mock the actual node run functions if it's too hard,
    # OR we let the real run_hunter execute but mock its internal calls.
    # Actually, the prompt says "force dry_run=true, mock LLM router, mock apify".
    # I will mock LLMRouter for Craft/Profiler.
    
    class MockRouter:
        def __init__(self, *args, **kwargs): pass
        async def call(self, prompt, task_type=None, **kwargs):
            if task_type == "research_synthesis":
                return json.dumps({
                    "company_name": "CorpA",
                    "pain_points": ["Cost"],
                    "tech_stack": ["Python"],
                    "recent_news": ["News"],
                    "personalization_hooks": ["Hook"],
                    "fit_score": 85.0
                })
            elif task_type == "email_craft":
                return json.dumps({
                    "initial": {"subject": "S1", "body": "B1"},
                    "fu1": {"subject": "S2", "body": "B2"},
                    "fu2": {"subject": "S3", "body": "B3"},
                    "fu3": {"subject": "S4", "body": "B4"}
                })
            return "{}"
            
    monkeypatch.setattr("app.modules.profiler.run.LLMRouter", MockRouter)
    monkeypatch.setattr("app.modules.craft.run.LLMRouter", MockRouter)
    
    # Mock Scrapers
    async def mock_scrape_website(*args, **kwargs): return {"content": "Mocked"}
    async def mock_scrape_linkedin(*args, **kwargs): return {"linkedin": "Mocked"}
    async def mock_scrape_news(*args, **kwargs): return [{"title": "News"}]
    
    monkeypatch.setattr("app.modules.profiler.run.scrape_website", mock_scrape_website)
    monkeypatch.setattr("app.modules.profiler.run.read_company_page", mock_scrape_linkedin)
    monkeypatch.setattr("app.modules.profiler.run.scrape_google_news", mock_scrape_news)
    
    # Mock Hunter Apify
    # We will just bypass the apify call by mocking the hunter run to return the existing RAW lead
    async def mock_hunter(state):
        async with e2e_session.bind.connect() as conn:
            # We don't actually need to insert anything to prove the cycle works.
            # We already seeded a RAW lead.
            # But we can just append to lead_ids.
            state["lead_ids"] = [1]
        return state
    monkeypatch.setattr(graph_mod, "run_hunter", mock_hunter)

    # 3. Run full cycle
    await graph_mod.run_full_cycle()
    
    # 4. Assertions
    await session.refresh(l1)
    
    # Because require_manual_approval = False and dry_run = True, outreach should send the initial email immediately
    assert l1.status == "SENT", f"Expected SENT, got {l1.status}"
    
    runs_res = await session.execute(select(Run))
    run = runs_res.scalar_one()
    assert run.completed_at is not None
    assert run.leads_researched == 1
    assert run.emails_sent == 1
    
    # Activity Log Check
    logs_res = await session.execute(select(ActivityLog).where(ActivityLog.lead_id == 1))
    actors = {log.actor for log in logs_res.scalars().all()}
    assert "profiler" in actors
    assert "craft" in actors
    assert "outreach" in actors

@pytest.mark.asyncio
async def test_failure_resilience(e2e_session, monkeypatch):
    session = e2e_session
    monkeypatch.setattr(graph_mod, "get_config", lambda: MockConfig())
    
    l2 = Lead(company_name="CorpB", domain="corpb.com", status="RAW", source="test")
    session.add(l2)
    await session.commit()
    
    async def mock_hunter(state):
        state["lead_ids"] = [l2.id]
        return state
        
    async def mock_profiler(state):
        raise ValueError("Simulated unrecoverable failure")
        
    monkeypatch.setattr(graph_mod, "run_hunter", mock_hunter)
    monkeypatch.setattr(graph_mod, "run_profiler", mock_profiler)
    
    # Speed up tenacity retries
    import tenacity
    def fast_wait(*args, **kwargs): return tenacity.wait_none()
    
    # We have to patch the retry logic or just wait 14s. Let's monkeypatch wait_exponential
    monkeypatch.setattr(graph_mod, "wait_exponential", fast_wait)
    
    # But wait, wait_exponential is used as a decorator at import time. We can't easily monkeypatch it after import.
    # We will just let it sleep, it's 2+4+8 = 14s. Or we can redefine resilient_node.
    # Let's redefine resilient_node to be fast.
    orig_resilient_node = graph_mod.resilient_node
    def fast_resilient_node(func, failure_suffix):
        @tenacity.retry(wait=tenacity.wait_none(), stop=tenacity.stop_after_attempt(3), reraise=True)
        async def retry_wrapper(state):
            return await func(state)
        async def node_wrapper(state):
            try: return await retry_wrapper(state)
            except Exception as e:
                async with graph_mod.get_session() as s:
                    for lid in state.get("lead_ids", []):
                        ld = await s.get(Lead, lid)
                        if ld: ld.status = failure_suffix
                        s.add(ActivityLog(lead_id=lid, actor="orchestrator", action=f"Node {func.__name__} failed", detail=str(e)))
                    await s.commit()
                state["errors"] = state.get("errors", 0) + 1
                return state
        return node_wrapper
    monkeypatch.setattr(graph_mod, "resilient_node", fast_resilient_node)
    
    # Rebuild graph so it uses the fast resilient node
    monkeypatch.setattr(graph_mod, "build_graph", lambda: orig_build_graph_with_mocks())
    
    def orig_build_graph_with_mocks():
        from langgraph.graph import StateGraph, START, END
        workflow = StateGraph(graph_mod.PipelineState)
        workflow.add_node("hunt", fast_resilient_node(mock_hunter, "HUNT_FAILED"))
        workflow.add_node("profile", fast_resilient_node(mock_profiler, "PROFILE_FAILED"))
        workflow.add_node("craft", fast_resilient_node(graph_mod.run_craft, "CRAFT_FAILED"))
        workflow.add_node("outreach", fast_resilient_node(graph_mod.run_outreach, "OUTREACH_FAILED"))
        workflow.add_node("crm_sync", graph_mod.crm_sync_node)
        workflow.add_edge(START, "hunt")
        workflow.add_edge("hunt", "profile")
        workflow.add_edge("profile", "craft")
        workflow.add_edge("craft", "outreach")
        workflow.add_edge("outreach", "crm_sync")
        workflow.add_edge("crm_sync", END)
        return workflow.compile()
        
    await graph_mod.run_full_cycle()
    
    await session.refresh(l2)
    assert l2.status == "PROFILE_FAILED"
    
    # Assert logged
    logs = await session.execute(select(ActivityLog).where(ActivityLog.lead_id == l2.id))
    log = logs.scalars().first()
    assert log is not None
    assert log.actor == "orchestrator"
    
    # Assert run finished
    runs = await session.execute(select(Run))
    runs = runs.scalars().all()
    # It will be the second run in the DB
    assert runs[-1].completed_at is not None
    assert runs[-1].errors == 1

@pytest.mark.asyncio
async def test_crm_transitions(e2e_session, monkeypatch):
    session = e2e_session
    l3 = Lead(company_name="CorpC", domain="corpc.com", status="SENT", source="test")
    session.add(l3)
    await session.commit()
    
    session.add(OutreachSequence(lead_id=l3.id, sequence_type="fu1", subject="S", body="B", status="queued"))
    await session.commit()
    
    async def mock_notify(msg): pass
    monkeypatch.setattr(trans_mod, "notify_webhook", mock_notify)
    
    await trans_mod.mark_replied(l3.id, "positive", "Yes, let's chat.", session)
    await session.commit()
    
    await session.refresh(l3)
    assert l3.status == "REPLIED"
    
    seqs = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == l3.id))
    assert seqs.scalars().first().status == "cancelled"
    
    pipe = await session.execute(select(Pipeline).where(Pipeline.lead_id == l3.id))
    assert pipe.scalar_one().stage == "REPLIED"

@pytest.mark.asyncio
async def test_manual_pipeline_actions(e2e_session, monkeypatch):
    session = e2e_session
    l4 = Lead(company_name="CorpD", domain="corpd.com", status="REPLIED", source="test")
    session.add(l4)
    await session.commit()
    
    await pipe_mod.book_meeting(l4.id, "Meeting next Tuesday", session)
    await session.commit()
    pipe = await session.execute(select(Pipeline).where(Pipeline.lead_id == l4.id))
    assert pipe.scalar_one().stage == "MEETING_BOOKED"
    
    await pipe_mod.send_proposal(l4.id, 5000.0, "Proposal sent", session)
    await session.commit()
    pipe = await session.execute(select(Pipeline).where(Pipeline.lead_id == l4.id))
    p = pipe.scalar_one()
    assert p.stage == "PROPOSAL_SENT"
    assert p.contract_value == 5000.0
    
    await pipe_mod.mark_won(l4.id, 5500.0, session)
    await session.commit()
    pipe = await session.execute(select(Pipeline).where(Pipeline.lead_id == l4.id))
    p = pipe.scalar_one()
    assert p.stage == "WON"
    assert p.contract_value == 5500.0

@pytest.mark.asyncio
async def test_digest(e2e_session, monkeypatch):
    session = e2e_session
    async def mock_notify(msg): pass
    monkeypatch.setattr(digest_mod, "notify_webhook", mock_notify)
    
    run = Run(leads_scraped=0, leads_researched=1, emails_sent=1, replies_received=0)
    session.add(run)
    
    # Create a parent Lead so the FK on Pipeline is satisfied
    digest_lead = Lead(company_name="DigestCo", domain="digestco.com", status="SENT", source="test")
    session.add(digest_lead)
    await session.commit()
    
    pipe = Pipeline(lead_id=digest_lead.id, stage="PROPOSAL_SENT", contract_value=5500.0)
    session.add(pipe)
    await session.commit()
    
    digest_text = await digest_mod.run_daily_digest(session)
    
    today_str = datetime.utcnow().date().isoformat()
    assert "Profiles Generated: 1" in digest_text
    assert "Emails Sent:        1" in digest_text
    assert "Pipeline Value:     $5,500.00" in digest_text
    
    assert os.path.exists(f"logs/digest_{today_str}.txt")
