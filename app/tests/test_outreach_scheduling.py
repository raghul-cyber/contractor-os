import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timedelta
import os
import json

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, text
from app.core.models import Base, Lead, OutreachSequence, EmailEvent
import app.modules.outreach.run as outreach_run_mod
import app.modules.outreach.reply_detector as reply_det_mod
import app.modules.outreach.sender as sender_mod
from app.core.scheduler import get_scheduler, register_jobs

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
async def temp_db_session_outreach(monkeypatch):
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

    monkeypatch.setattr(outreach_run_mod, "get_session", MockSessionContext)
    monkeypatch.setattr(reply_det_mod, "get_session", MockSessionContext)
    monkeypatch.setattr(sender_mod, "get_session", MockSessionContext)
    
    async with SessionLocal() as session:
        yield session

# ---------------------------------------------------------
# 1. dry_run guarantee test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_dry_run_guarantee(temp_db_session_outreach, monkeypatch):
    monkeypatch.setattr(sender_mod, "get_config", lambda: MockConfig())
    
    real_send_called = False
    
    async def fake_execute_send(*args, **kwargs):
        nonlocal real_send_called
        real_send_called = True
        return {"id": "fake", "status": "sent"}
        
    monkeypatch.setattr(sender_mod, "_execute_send", fake_execute_send)
    
    res = await sender_mod.send_email(to="test@test.com", subject="S", body="B", dry_run=True)
    
    assert res["status"] == "sent"
    assert res.get("dry_run") is True
    assert not real_send_called, "Real send was invoked despite dry_run=True!"

# ---------------------------------------------------------
# 2. Full cycle scheduling test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_full_cycle_scheduling(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(outreach_run_mod, "get_config", lambda: MockConfig())
    
    lead = Lead(company_name="Test", domain="test.com", status="DRAFTED", email="test@test.com", source="test")
    session.add(lead)
    await session.commit()
    
    # Approved initial
    session.add(OutreachSequence(lead_id=lead.id, sequence_type="initial", subject="S", body="B", status="approved"))
    session.add(OutreachSequence(lead_id=lead.id, sequence_type="fu1", subject="S", body="B", status="draft"))
    session.add(OutreachSequence(lead_id=l.id if 'l' in locals() else lead.id, sequence_type="fu2", subject="S", body="B", status="draft"))
    session.add(OutreachSequence(lead_id=lead.id, sequence_type="fu3", subject="S", body="B", status="draft"))
    await session.commit()
    
    before_run = datetime.utcnow()
    await outreach_run_mod.run_outreach({})
    after_run = datetime.utcnow()
    
    await session.refresh(lead)
    assert lead.status == "SENT"
    
    seqs_res = await session.execute(select(OutreachSequence).where(OutreachSequence.lead_id == lead.id))
    seqs = {s.sequence_type: s for s in seqs_res.scalars().all()}
    
    assert seqs["initial"].status == "sent"
    sent_at_dt = datetime.fromisoformat(seqs["initial"].sent_at)
    
    assert seqs["fu1"].status == "queued"
    fu1_dt = datetime.fromisoformat(seqs["fu1"].scheduled_at)
    assert abs((fu1_dt - sent_at_dt).total_seconds() - 5*86400) < 5 # 5 days
    
    assert seqs["fu2"].status == "queued"
    fu2_dt = datetime.fromisoformat(seqs["fu2"].scheduled_at)
    assert abs((fu2_dt - sent_at_dt).total_seconds() - 10*86400) < 5 # 10 days
    
    assert seqs["fu3"].status == "queued"
    fu3_dt = datetime.fromisoformat(seqs["fu3"].scheduled_at)
    assert abs((fu3_dt - sent_at_dt).total_seconds() - 15*86400) < 5 # 15 days

# ---------------------------------------------------------
# 3. Approval-gate negative test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_approval_gate_negative(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(outreach_run_mod, "get_config", lambda: MockConfig())
    
    lead = Lead(company_name="Gate", domain="gate.com", status="DRAFTED", email="gate@test.com", source="test")
    session.add(lead)
    await session.commit()
    
    session.add(OutreachSequence(lead_id=lead.id, sequence_type="initial", subject="S", body="B", status="draft")) # NOT approved
    await session.commit()
    
    await outreach_run_mod.run_outreach({})
    
    await session.refresh(lead)
    assert lead.status == "DRAFTED" # Unchanged
    
    events_res = await session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    assert len(events_res.scalars().all()) == 0

# ---------------------------------------------------------
# 4. Send-limit test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_send_limit_enforcement(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    
    class LimitConfig:
        class System:
            batch_size = 10
            class Outreach:
                dry_run = True
                daily_send_limit = 3
                follow_up_intervals_days = [5, 10, 15]
                send_backend = "smtp"
            outreach = Outreach()
        system = System()
        
    monkeypatch.setattr(outreach_run_mod, "get_config", lambda: LimitConfig())
    monkeypatch.setattr(sender_mod, "get_config", lambda: LimitConfig())
    
    past_date = (datetime.utcnow() - timedelta(days=1)).isoformat()
    
    for i in range(5):
        l = Lead(company_name=f"L{i}", domain=f"{i}.com", status="SENT", email=f"{i}@test.com", source="test")
        session.add(l)
        await session.flush()
        session.add(OutreachSequence(lead_id=l.id, sequence_type="fu1", subject="S", body="B", status="queued", scheduled_at=past_date))
    await session.commit()
    
    await outreach_run_mod.check_due_followups_job()
    
    seq_res = await session.execute(select(OutreachSequence).where(OutreachSequence.sequence_type == "fu1"))
    seqs = seq_res.scalars().all()
    
    sent = sum(1 for s in seqs if s.status == "sent")
    queued = sum(1 for s in seqs if s.status == "queued")
    
    assert sent == 3
    assert queued == 2

# ---------------------------------------------------------
# 5. Scheduler persistence test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_scheduler_persistence():
    import tempfile
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    db_url = f"sqlite:///{db_path}"
    
    try:
        def get_test_scheduler():
            jobstores = {'default': SQLAlchemyJobStore(url=db_url, tablename='apscheduler_jobs')}
            return AsyncIOScheduler(jobstores=jobstores)
            
        s1 = get_test_scheduler()
        s1.start()
        register_jobs(s1, MockConfig())
        
        # Verify 4 jobs in memory
        assert len(s1.get_jobs()) == 4
        s1.shutdown()
        
        # Query DB directly to prove it's in SQLite
        from sqlalchemy import create_engine
        engine = create_engine(db_url)
        with engine.connect() as conn:
            res = conn.execute(text("SELECT id FROM apscheduler_jobs")).fetchall()
            db_ids = {r[0] for r in res}
            assert "main_cycle" in db_ids
            assert "followup_check" in db_ids
            assert "inbox_poll" in db_ids
            assert "daily_digest" in db_ids
        engine.dispose()
        
        # Bring up s2 without calling register_jobs
        s2 = get_test_scheduler()
        s2.start()
        jobs2 = s2.get_jobs()
        assert len(jobs2) == 4
        s2.shutdown()
        
    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                pass

# ---------------------------------------------------------
# 6. Reply classification test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_reply_classification(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(reply_det_mod, "get_config", lambda: MockConfig())
    
    lead = Lead(company_name="Rep", domain="rep.com", status="SENT", email="target@test.com", source="test")
    session.add(lead)
    await session.commit()
    
    async def mock_fetch_unread():
        return [{"sender": "target@test.com", "body": "yes, let's chat, sounds great", "subject": "Re: Hook"}]
    monkeypatch.setattr(reply_det_mod, "fetch_unread_emails", mock_fetch_unread)
    
    class MockRouter:
        async def call(self, prompt, **kwargs): return "positive"
    monkeypatch.setattr(reply_det_mod, "LLMRouter", lambda cfg: MockRouter())
    
    hook_called_with = []
    def spy_mark_replied(lead_id):
        hook_called_with.append(lead_id)
    monkeypatch.setattr(reply_det_mod, "mark_replied", spy_mark_replied)
    
    await reply_det_mod.poll_inbox_job()
    
    assert hook_called_with == [lead.id]
    
    events_res = await session.execute(select(EmailEvent).where(EmailEvent.lead_id == lead.id))
    events = events_res.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "replied"
    assert events[0].sentiment == "positive"
    assert "yes, let's chat" in events[0].raw_snippet

# ---------------------------------------------------------
# 7. GHOSTED transition test
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_ghosted_transition(temp_db_session_outreach, monkeypatch):
    session = temp_db_session_outreach
    monkeypatch.setattr(outreach_run_mod, "get_config", lambda: MockConfig())
    monkeypatch.setattr(sender_mod, "get_config", lambda: MockConfig())
    
    l_ghost = Lead(company_name="Ghost", domain="gh.com", status="FU3_SENT", source="test")
    l_recent = Lead(company_name="Recent", domain="rc.com", status="FU3_SENT", source="test")
    session.add_all([l_ghost, l_recent])
    await session.commit()
    
    # ghost > 5 days ago (e.g. 6)
    six_days_ago = (datetime.utcnow() - timedelta(days=6)).isoformat()
    session.add(OutreachSequence(lead_id=l_ghost.id, sequence_type="fu3", subject="S", body="B", status="sent", sent_at=six_days_ago))
    
    # recent = 2 days ago
    two_days_ago = (datetime.utcnow() - timedelta(days=2)).isoformat()
    session.add(OutreachSequence(lead_id=l_recent.id, sequence_type="fu3", subject="S", body="B", status="sent", sent_at=two_days_ago))
    await session.commit()
    
    await outreach_run_mod.check_due_followups_job()
    
    await session.refresh(l_ghost)
    await session.refresh(l_recent)
    
    assert l_ghost.status == "GHOSTED"
    assert l_recent.status == "FU3_SENT"
