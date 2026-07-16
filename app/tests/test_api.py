import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from datetime import date
from contextlib import asynccontextmanager

from app.main import app
from app.core.models import Base, Lead, OutreachSequence, EmailEvent, Pipeline, Run
import app.core.db as db_mod

client = TestClient(app)

from sqlalchemy.pool import StaticPool

@pytest_asyncio.fixture
async def e2e_session(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "async_session_maker", SessionLocal)
    
    async with SessionLocal() as session:
        yield session

@pytest.mark.asyncio
async def test_get_leads(e2e_session):
    l1 = Lead(company_name="Company1", domain="test1.com", status="DRAFTED", source="test")
    l2 = Lead(company_name="Company2", domain="test2.com", status="SENT", source="test")
    e2e_session.add_all([l1, l2])
    await e2e_session.commit()
        
    response = client.get("/api/leads")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    
    # Filter by DRAFTED
    response_draft = client.get("/api/leads?status=DRAFTED")
    data_draft = response_draft.json()
    assert all(d["status"] == "DRAFTED" for d in data_draft)

@pytest.mark.asyncio
async def test_get_lead_detail(e2e_session):
    l = Lead(company_name="Company3", domain="test3.com", status="DRAFTED", source="test", profile_json='{"pain_points": ["Cost"]}')
    e2e_session.add(l)
    await e2e_session.commit()
    
    seq = OutreachSequence(lead_id=l.id, sequence_type="initial", subject="S1", body="B1", status="draft")
    e2e_session.add(seq)
    await e2e_session.commit()
    
    l_id = l.id
        
    response = client.get(f"/api/leads/{l_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["lead"]["company_name"] == "Company3"
    assert data["profile"]["pain_points"][0] == "Cost"
    assert len(data["sequences"]) == 1
    assert data["sequences"][0]["subject"] == "S1"

@pytest.mark.asyncio
async def test_approve_sequence(e2e_session):
    l = Lead(company_name="Company4", domain="test4.com", status="DRAFTED", source="test")
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

@pytest.mark.asyncio
async def test_trigger_run(e2e_session):
    response = client.post("/api/run/trigger")
    assert response.status_code == 200
    assert response.json()["status"] == "started"

@pytest.mark.asyncio
async def test_stats_today(e2e_session):
    from datetime import datetime
    today = datetime.utcnow().date().isoformat()
    
    response_before = client.get("/api/stats/today")
    assert response_before.status_code == 200
    leads_before = response_before.json()["leads_added_today"]
    
    l = Lead(company_name="Company5", domain="test5.com", status="RAW", source="test")
    e2e_session.add(l)
    await e2e_session.commit()
        
    response_after = client.get("/api/stats/today")
    assert response_after.status_code == 200
    assert response_after.json()["leads_added_today"] == leads_before + 1
