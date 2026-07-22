import pytest
import os
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.models import Base, Lead
from app.modules.hunter.dedup import normalize_domain, insert_lead_if_new
from app.modules.hunter.sources.manual_import import import_from_csv
from sqlalchemy import select

# 1. Parametrized tests for normalize_domain
@pytest.mark.parametrize(
    "input_url,expected",
    [
        ("https://Example.com/", "example.com"),
        ("www.example.com", "example.com"),
        ("EXAMPLE.COM/about", "example.com"),
        ("example.com", "example.com"),
        ("http://www.TEST.co.uk/?q=1", "test.co.uk"),
        ("  HTTPS://SPACED.COM  ", "spaced.com")
    ]
)
def test_normalize_domain(input_url, expected):
    assert normalize_domain(input_url) == expected

import pytest_asyncio

# 2. Async tests for DB insert/dedup
@pytest_asyncio.fixture
async def temp_db_session():
    # Use an in-memory SQLite DB for testing
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
async def test_insert_lead_if_new(temp_db_session):
    # Insert first lead
    raw1 = {"company_name": "First", "website": "https://TestDomain.com/xyz"}
    inserted1 = await insert_lead_if_new(temp_db_session, raw1)
    await temp_db_session.commit()
    assert inserted1 is True
    
    # Attempt insert of duplicate (different case/protocol)
    raw2 = {"company_name": "Duplicate", "domain": "www.testdomain.com"}
    inserted2 = await insert_lead_if_new(temp_db_session, raw2)
    await temp_db_session.commit()
    assert inserted2 is False
    
    # Assert exactly 1 row exists
    res = await temp_db_session.execute(select(Lead))
    leads = res.scalars().all()
    assert len(leads) == 1
    assert leads[0].domain == "testdomain.com"
    assert leads[0].company_name == "First"

@pytest.mark.asyncio
async def test_import_from_csv(temp_db_session):
    csv_path = os.path.join(os.path.dirname(__file__), "fixtures", "leads_sample.csv")
    
    res = await import_from_csv(csv_path, temp_db_session)
    assert res["inserted"] == 4
    assert res["skipped_duplicates"] == 1
    assert res["rows_read"] == 5
    
    # Query leads table
    db_res = await temp_db_session.execute(select(Lead))
    leads = db_res.scalars().all()
    
    assert len(leads) == 4
    for lead in leads:
        assert lead.status == "RAW"
        assert lead.source == "manual_csv"
    
    domains = {l.domain for l in leads}
    assert domains == {"alpha.com", "beta.com", "gamma.com", "delta.co.uk"}
