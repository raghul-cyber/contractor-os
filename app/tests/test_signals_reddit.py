import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event as sa_event, select, text
from app.core.models import Base, SignalHit
from app.modules.signals.reddit_listener import poll_reddit_signals

@pytest_asyncio.fixture
async def temp_db_session_signals(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    @sa_event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON;")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    
    async with SessionLocal() as session:
        yield session

@pytest.mark.asyncio
@patch("app.modules.signals.reddit_listener.praw.Reddit")
@patch("app.modules.signals.reddit_listener.notify_webhook")
@patch("app.modules.signals.reddit_listener.get_config")
@patch("app.modules.signals.reddit_listener.os.getenv")
async def test_reddit_signals_matching(mock_getenv, mock_get_config, mock_notify, mock_reddit_class, temp_db_session_signals):
    # Mock config
    mock_config = MagicMock()
    mock_config.system.signals.reddit.enabled = True
    mock_config.system.signals.reddit.subreddits = ["devops"]
    mock_config.system.signals.reddit.keywords = ["need a developer"]
    mock_config.system.signals.reddit.lookback_hours = 2
    mock_get_config.return_value = mock_config

    mock_getenv.side_effect = lambda k, default=None: "mock_val" if k in ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"] else default

    # Mock PRAW
    mock_reddit = MagicMock()
    mock_reddit.read_only = True
    mock_reddit_class.return_value = mock_reddit
    
    mock_sub = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub
    
    import time
    now = time.time()

    # Create mock submissions
    sub1 = MagicMock()
    sub1.created_utc = now
    sub1.title = "I need a developer for my project"
    sub1.selftext = "Please help"
    sub1.permalink = "/r/devops/1"
    sub1.author.name = "user1"

    sub2 = MagicMock()
    sub2.created_utc = now
    sub2.title = "Just sharing some thoughts"
    sub2.selftext = "Hello world"
    sub2.permalink = "/r/devops/2"
    sub2.author.name = "user2"

    mock_sub.new.return_value = [sub1, sub2]
    mock_sub.comments.return_value = []

    # Run poll
    await poll_reddit_signals(temp_db_session_signals)

    # Assertions
    hits_res = await temp_db_session_signals.execute(select(SignalHit))
    hits = hits_res.scalars().all()

    # Only sub1 should match
    assert len(hits) == 1
    assert hits[0].post_url == "https://reddit.com/r/devops/1"
    assert hits[0].matched_keyword == "need a developer"
    assert hits[0].notified == 1

    # Ensure no email/phone field exists on table using PRAGMA
    pragma_res = await temp_db_session_signals.execute(text("PRAGMA table_info(signal_hits)"))
    columns = [row[1] for row in pragma_res.fetchall()]
    assert "email" not in columns
    assert "phone" not in columns

    # Verify notification was sent
    assert mock_notify.call_count == 1
    notify_args = mock_notify.call_args[0][0]
    assert "user1" in notify_args
    assert "https://reddit.com/r/devops/1" in notify_args

    # Second poll should skip duplicates (because post_url already exists)
    mock_notify.reset_mock()
    await poll_reddit_signals(temp_db_session_signals)
    
    # Should still be 1 hit in DB
    hits_res2 = await temp_db_session_signals.execute(select(SignalHit))
    assert len(hits_res2.scalars().all()) == 1
    
    # notify_webhook should not be called again
    assert mock_notify.call_count == 0
