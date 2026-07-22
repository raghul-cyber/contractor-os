import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock

import app.modules.hunter.run as hunter_run
from app.core.models import Lead, ActivityLog
from app.modules.hunter.sources.bs4_email_fallback import fast_extract_email, parse_emails_from_html
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import event as sa_event
from app.core.models import Base

@pytest_asyncio.fixture
async def e2e_session():
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
async def test_bs4_parse_emails():
    # Test mailto: link
    html_with_mailto = "<html><body><a href='mailto:sales@example.com'>Contact Us</a></body></html>"
    assert parse_emails_from_html(html_with_mailto) == "sales@example.com"
    
    # Test regex in text
    html_with_regex = "<html><body>Reach us at info@mycompany.io for more info.</body></html>"
    assert parse_emails_from_html(html_with_regex) == "info@mycompany.io"
    
    # Test false positives
    html_with_fp = "<html><body>Send to example@example.com or look at logo.png</body></html>"
    assert parse_emails_from_html(html_with_fp) is None

@pytest.mark.asyncio
@patch("app.modules.hunter.sources.bs4_email_fallback.Fetcher")
@patch("app.modules.hunter.run.extract_contacts")
async def test_hunter_email_fallback_path(mock_extract_contacts, mock_fetcher_cls, e2e_session):
    # Setup mock fetcher to return a page with a mailto link
    mock_fetcher = MagicMock()
    mock_fetcher_cls.return_value = mock_fetcher
    
    mock_resp_with_email = MagicMock()
    mock_resp_with_email.status = 200
    mock_resp_with_email.body = b"something" * 100
    mock_resp_with_email.text = "<html><body>" + "padding "*100 + "<a href='mailto:fast@found.com'>Email</a></body></html>"
    
    # Session setup
    session = e2e_session
    lead_has_website = Lead(company_name="Fast", domain="fast.com", website="fast.com", status="RAW", source="test")
    lead_no_email = Lead(company_name="Slow", domain="slow.com", website="slow.com", status="RAW", source="test")
    
    session.add_all([lead_has_website, lead_no_email])
    await session.commit()
    
    # Define a side effect for the mock fetcher
    def fetch_side_effect(url):
        if "fast.com" in url:
            return mock_resp_with_email
        else:
            mock_no_email = MagicMock()
            mock_no_email.status = 200
            mock_no_email.body = b"something" * 100
            mock_no_email.text = "<html><body>" + "padding "*100 + "No email here!</body></html>"
            return mock_no_email
            
    mock_fetcher.get.side_effect = fetch_side_effect
    
    # Apify mock
    mock_extract_contacts.return_value = {"email": "apify@slow.com"}
    
    # Run the backfill portion of hunter_run
    # We can just call run_hunter with everything disabled except contact backfill
    # But contact backfill is currently unconditional for RAW leads without email
    
    # Create a minimal config mock to skip apify maps, jobboards, etc.
    class MockTargeting:
        sectors = []
        locations = []
    class MockConfigTargets:
        targeting = MockTargeting()
    class MockHunterConfig:
        use_jobboard_signals = False
        use_crunchbase = False
        use_directories = False
        use_paid_leadscraper = False
    class MockSystemConfig:
        hunter = MockHunterConfig()
    class MockConfig:
        targets = MockConfigTargets()
        system = MockSystemConfig()
        
    class MockSessionContext:
        async def __aenter__(self):
            return session
        async def __aexit__(self, exc_type, exc, tb):
            pass
            
    with patch("app.modules.hunter.run.get_config", return_value=MockConfig()):
        with patch("app.modules.hunter.run.get_session", return_value=MockSessionContext()):
            with patch("app.modules.hunter.run.scrape_local_search", return_value=[]):
                with patch("app.modules.hunter.run.scrape_google_maps", return_value=[]):
                    await hunter_run.run_hunter({})
                
    # Verify results
    await session.refresh(lead_has_website)
    await session.refresh(lead_no_email)
    
    # lead_has_website should have gotten email from BS4
    assert lead_has_website.email == "fast@found.com"
    # lead_no_email should have fallen through to Apify
    assert lead_no_email.email == "apify@slow.com"
    
    # Verify mock_extract_contacts was ONLY called for slow.com, not fast.com
    mock_extract_contacts.assert_called_once_with("slow.com")
