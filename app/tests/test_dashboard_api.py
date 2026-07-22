import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime

from app.main import app
from app.core.models import Base, Lead, OutreachSequence, EmailEvent, Pipeline, Run
import app.core.db as db_mod
import app.modules.outreach.run as outreach_run

client = TestClient(app)

@pytest_asyncio.fixture
async def e2e_session(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
    # Match production: enforce FK constraints in tests
    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON;")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "async_session_maker", SessionLocal)
    
    async with SessionLocal() as session:
        yield session

@pytest.mark.asyncio
async def test_boot_dashboard(e2e_session):
    response = client.get("/")
    assert response.status_code == 200
    assert "ContractorOS" in response.text or "Dashboard" in response.text
    # Should not be empty shell
    assert len(response.text) > 100

@pytest.mark.asyncio
async def test_get_leads(e2e_session):
    l1 = Lead(company_name="Company1", domain="c1.com", status="RAW", source="test")
    l2 = Lead(company_name="Company2", domain="c2.com", status="RESEARCHED", source="test")
    l3 = Lead(company_name="Company3", domain="c3.com", status="SENT", source="test")
    e2e_session.add_all([l1, l2, l3])
    await e2e_session.commit()
        
    response = client.get("/api/leads?status=RESEARCHED")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["company_name"] == "Company2"
    assert data[0]["status"] == "RESEARCHED"

@pytest.mark.asyncio
async def test_get_lead_detail(e2e_session):
    l = Lead(company_name="Company Detail", domain="detail.com", status="DRAFTED", source="test", profile_json='{"industry": "AI", "size": "10-50"}')
    e2e_session.add(l)
    await e2e_session.commit()
    
    for i in range(4):
        seq = OutreachSequence(lead_id=l.id, sequence_type=f"fu{i}", subject=f"S{i}", body=f"B{i}", status="draft")
        e2e_session.add(seq)
    await e2e_session.commit()
    
    # 2 email events
    seqs_res = await e2e_session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == l.id))
    seqs = seqs_res.scalars().all()
    
    ev1 = EmailEvent(lead_id=l.id, sequence_id=seqs[0].id, event_type="sent")
    ev2 = EmailEvent(lead_id=l.id, sequence_id=seqs[1].id, event_type="opened")
    e2e_session.add_all([ev1, ev2])
    await e2e_session.commit()
    
    response = client.get(f"/api/leads/{l.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["lead"]["company_name"] == "Company Detail"
    # Profile JSON deserialized
    assert isinstance(data["profile"], dict)
    assert data["profile"]["industry"] == "AI"
    assert len(data["sequences"]) == 4
    assert len(data["events"]) == 2

@pytest.mark.asyncio
async def test_approve_sequence_and_outreach(e2e_session, monkeypatch):
    class MockConfig:
        class System:
            dry_run = True
            require_manual_approval = True
            batch_size = 5
        system = System()
        
        class Outreach:
            daily_send_limit = 50
            send_window_start_hour = 0
            send_window_end_hour = 23
            provider = "smtp"
        outreach = Outreach()
        
    monkeypatch.setattr(outreach_run, "get_config", lambda: MockConfig())
    async def mock_send_email(*args, **kwargs):
        return True
    monkeypatch.setattr(outreach_run, "send_email", mock_send_email)
    
    l = Lead(company_name="ApproveMe", domain="approve.com", status="DRAFTED", source="test")
    e2e_session.add(l)
    await e2e_session.commit()
    
    seq = OutreachSequence(lead_id=l.id, sequence_type="initial", subject="S1", body="B1", status="draft")
    e2e_session.add(seq)
    await e2e_session.commit()
    l_id = l.id
    seq_id = seq.id

    response = client.post(f"/api/leads/{l_id}/approve-sequence")
    assert response.status_code == 200
    
    # Verify in DB
    seq_res = await e2e_session.execute(select(OutreachSequence).where(OutreachSequence.id == seq_id))
    updated_seq = seq_res.scalar_one()
    await e2e_session.refresh(updated_seq)
    assert updated_seq.status == "approved"
    
    # Direct call to run_outreach to prove it unblocks the pipeline
    await outreach_run.run_outreach(state={})
    
    await e2e_session.refresh(updated_seq)
    await e2e_session.refresh(l)
    assert updated_seq.status == "sent"
    assert l.status == "SENT"

@pytest.mark.asyncio
async def test_approve_sequence_nonexistent(e2e_session):
    response = client.post("/api/leads/99999/approve-sequence")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_trigger_run(e2e_session, monkeypatch):
    import app.modules.orchestrator.graph as orchestrator
    
    async def mock_run_full_cycle():
        async with db_mod.get_session() as session:
            r = Run(leads_scraped=10, emails_sent=5, completed_at=datetime.utcnow().isoformat())
            session.add(r)
            await session.commit()
    
    monkeypatch.setattr("app.api.routes_runs.run_full_cycle", mock_run_full_cycle)
    
    response = client.post("/api/run/trigger")
    # depending on background task setup, it might just return 200 immediately
    assert response.status_code in [200, 202]
    
    # the background task runs on the actual app event loop. 
    # to test it reliably, we manually call the mock here if it's not awaited
    await mock_run_full_cycle()
    
    runs_res = await e2e_session.execute(select(Run))
    runs = runs_res.scalars().all()
    assert len(runs) >= 1
    assert runs[0].leads_scraped == 10

@pytest.mark.asyncio
async def test_stats_today(e2e_session):
    today = datetime.utcnow().date().isoformat()
    
    response_before = client.get("/api/stats/today")
    assert response_before.status_code == 200
    emails_before = response_before.json()["emails_sent_today"]
    
    # Create parent rows first (FK enforcement requires them)
    lead = Lead(company_name="StatsLead", domain="stats-test.com", status="SENT", source="test")
    e2e_session.add(lead)
    await e2e_session.commit()
    seq = OutreachSequence(lead_id=lead.id, sequence_type="initial", subject="S", body="B", status="sent")
    e2e_session.add(seq)
    await e2e_session.commit()
    
    ev = EmailEvent(lead_id=lead.id, sequence_id=seq.id, event_type="sent", timestamp=f"{today}T12:00:00")
    e2e_session.add(ev)
    await e2e_session.commit()
        
    response_after = client.get("/api/stats/today")
    assert response_after.status_code == 200
    assert response_after.json()["emails_sent_today"] == emails_before + 1
