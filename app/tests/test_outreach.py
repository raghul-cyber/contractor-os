import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timedelta
import os

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, text
from app.core.models import Base, Lead, OutreachSequence, EmailEvent
import app.modules.outreach.run as outreach_run_mod
import app.modules.outreach.reply_detector as reply_det_mod
from app.core.scheduler import get_scheduler, register_jobs

class MockConfig:
    class System:
        batch_size = 5
        cycle_interval_hours = 6
        class Craft:
            require_manual_approval = False
        craft = Craft()
        class OutreachSystem:
            dry_run = True
            send_backend = "smtp"
        outreach = OutreachSystem()
        class SignalsMock:
            class RedditMock:
                enabled = True
                poll_interval_minutes = 30
            reddit = RedditMock()
        signals = SignalsMock()
    system = System()
    
    class OutreachRoot:
        class MockIdentity:
            def __init__(self, email, limit):
                self.email = email
                self.daily_send_limit = limit
        
        sending_identities = [MockIdentity("test1@ahixlight.com", 2)]
        follow_up_intervals_days = [5, 10, 15]
    outreach = OutreachRoot()

@pytest_asyncio.fixture
async def temp_db_session_outreach(monkeypatch):
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

    monkeypatch.setattr(outreach_run_mod, "get_session", MockSessionContext)
    monkeypatch.setattr(reply_det_mod, "get_session", MockSessionContext)
    import app.modules.outreach.sender as sender_mod
    monkeypatch.setattr(sender_mod, "get_session", MockSessionContext)
    
    async def fake_execute_send(*args, **kwargs):
        return {"id": "fake_id", "status": "sent"}
    monkeypatch.setattr(sender_mod, "_execute_send", fake_execute_send)
    
    async with SessionLocal() as session:
        yield session

@pytest.mark.asyncio
async def test_outreach_dry_run_scheduling(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(outreach_run_mod, "get_config", lambda: MockConfig())
    
    # 1. Setup a DRAFTED lead
    lead = Lead(company_name="Test", domain="test.com", status="DRAFTED", email="test@test.com", source="test")
    session.add(lead)
    await session.commit()
    
    # 2. Add 4 draft sequences
    for seq_type in ["initial", "fu1", "fu2", "fu3"]:
        session.add(OutreachSequence(lead_id=lead.id, sequence_type=seq_type, subject="S", body="B", status="draft"))
    await session.commit()
    
    # 3. Run outreach
    await outreach_run_mod.run_outreach({})
    
    # 4. Verify Transitions
    await session.refresh(lead)
    assert lead.status == "SENT"
    
    seqs_res = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == lead.id))
    seqs = seqs_res.scalars().all()
    
    for seq in seqs:
        if seq.sequence_type == "initial":
            assert seq.status == "sent"
            assert seq.sent_at is not None
        else:
            assert seq.status == "queued"
            assert seq.scheduled_at is not None
            
    # Verify EmailEvents written
    events_res = await session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    events = events_res.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "sent"

@pytest.mark.asyncio
async def test_outreach_daily_limit(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(outreach_run_mod, "get_config", lambda: MockConfig())
    import app.modules.outreach.sender as sender_mod
    monkeypatch.setattr(sender_mod, "get_config", lambda: MockConfig())
    
    # Add 3 leads
    leads = []
    for i in range(3):
        l = Lead(company_name=f"Test{i}", domain=f"t{i}.com", status="DRAFTED", email=f"t{i}@test.com", source="test")
        session.add(l)
        leads.append(l)
    await session.commit()
    
    for l in leads:
        session.add(OutreachSequence(lead_id=l.id, sequence_type="initial", subject="S", body="B", status="draft"))
        session.add(OutreachSequence(lead_id=l.id, sequence_type="fu1", subject="S", body="B", status="draft"))
    await session.commit()
    
    # Run outreach (daily limit is 2)
    await outreach_run_mod.run_outreach({})
    
    # Refresh leads
    for l in leads:
        await session.refresh(l)
        
    # Exactly 2 should be SENT, 1 should be DRAFTED still
    sent_count = sum(1 for l in leads if l.status == "SENT")
    draft_count = sum(1 for l in leads if l.status == "DRAFTED")
    
    assert sent_count == 2
    assert draft_count == 1

@pytest.mark.asyncio
async def test_reply_detector(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(reply_det_mod, "get_config", lambda: MockConfig())
    
    # 1. Setup Lead
    lead = Lead(company_name="Test", domain="test.com", status="SENT", email="target@test.com", source="test")
    session.add(lead)
    await session.commit()
    
    # 2. Mock IMAP
    async def mock_fetch_unread():
        return [{"sender": "target@test.com", "body": "Yes we would love to talk! Very positive.", "subject": "Re: S"}]
    monkeypatch.setattr(reply_det_mod, "fetch_unread_emails", mock_fetch_unread)
    
    # 3. Mock Router to return "positive"
    class MockRouter:
        async def call(self, prompt, **kwargs): return "positive"
    monkeypatch.setattr(reply_det_mod, "LLMRouter", lambda cfg: MockRouter())
    
    # 4. Spy on CRM Hook
    hook_called_with = []
    async def spy_mark_replied(lead_id, sentiment, snippet, session):
        hook_called_with.append(lead_id)
    monkeypatch.setattr(reply_det_mod, "mark_replied", spy_mark_replied)
    
    # Run Job
    await reply_det_mod.poll_inbox_job()
    
    # Assert
    assert len(hook_called_with) == 1
    assert hook_called_with[0] == lead.id
    
    events_res = await session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    events = events_res.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "replied"
    assert events[0].sentiment == "positive"

@pytest.mark.asyncio
async def test_apscheduler_restarts():
    # Write a small script or test the actual scheduler initialization
    # Since we need to test persistence in sqlite, we will use a real temp file DB
    import sqlite3
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    db_url = f"sqlite:///{db_path}"
    
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        import app.core.scheduler as sched_mod
        
        def get_test_scheduler():
            jobstores = {'default': SQLAlchemyJobStore(url=db_url, tablename='apscheduler_jobs')}
            return AsyncIOScheduler(jobstores=jobstores)
            
        # 1. Initialize and register
        s1 = get_test_scheduler()
        s1.start()
        sched_mod.register_jobs(s1, MockConfig())
        assert len(s1.get_jobs()) == 6  # 4 original + daily_backup + reddit_signals
        s1.shutdown()
        
        # 2. Re-initialize, don't register, check if jobs exist
        s2 = get_test_scheduler()
        s2.start()
        jobs = s2.get_jobs()
        assert len(jobs) == 6  # 4 original + daily_backup
        s2.shutdown()
        
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                pass
