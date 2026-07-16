import os
import sys
import asyncio
from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import engine, get_session
from app.modules.hunter.dedup import normalize_domain, insert_lead_if_new
from app.modules.hunter.sources.manual_import import import_from_csv
from app.modules.hunter.sources.utils import run_with_retry
from app.modules.hunter.run import run_hunter

async def verify():
    print("--- Verifying Phase 3 Hunter Module ---")
    
    # 1. normalize_domain
    assert normalize_domain("https://Example.com/") == "example.com"
    assert normalize_domain("www.example.com") == "example.com"
    assert normalize_domain("EXAMPLE.COM/about") == "example.com"
    assert normalize_domain("example.com") == "example.com"
    print("[x] normalize_domain handles variations correctly.")

    # Clean DB
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM leads"))
        
    # 2. insert_lead_if_new
    async with get_session() as session:
        res1 = await insert_lead_if_new(session, {"company_name": "Test1", "website": "https://test.com/"})
        res2 = await insert_lead_if_new(session, {"company_name": "Test2", "website": "www.TEST.com/contact"})
        await session.commit()
        
        assert res1 is True
        assert res2 is False
    print("[x] insert_lead_if_new correctly skips duplicates.")

    # 3. CSV Import
    csv_path = "test_leads.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("company_name,website,email\n")
        f.write("Company A,https://a.com,a@a.com\n")
        f.write("Company B,b.com/hello,b@b.com\n")
        f.write("Company C,www.c.com,c@c.com\n")
        f.write("Company D,d.com,\n")
        f.write("Company A Dup,A.COM,a2@a.com\n") # Duplicate

    async with get_session() as session:
        res = await import_from_csv(csv_path, session)
        assert res["inserted"] == 4
        assert res["skipped_duplicates"] == 1
    os.remove(csv_path)
    print("[x] Manual CSV import handles fixtures and duplicates correctly.")

    # 4. Apify retry + run_hunter resilience
    # We will mock the Apify function inside run_hunter by monkeypatching
    import app.modules.hunter.run as run_mod
    
    attempts = 0
    async def mock_scrape_google_maps(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("Simulated Apify Error")

    run_mod.scrape_google_maps = mock_scrape_google_maps
    
    # This shouldn't raise, should just log and continue
    state = {"csv_path": None}
    res = await run_hunter(state)
    assert res["hunter_inserted"] == 0 # no maps inserted
    # It retries in scrape_google_maps ? Wait, scrape_google_maps uses run_with_retry internally!
    # I mocked scrape_google_maps directly, so it bypassed run_with_retry.
    # Let me mock client.actor instead to test run_with_retry properly.

async def verify_retry():
    from app.modules.hunter.sources.apify_maps import scrape_google_maps
    from apify_client import ApifyClientAsync
    
    class MockActor:
        def __init__(self, *args, **kwargs):
            self.attempts = 0
            
        async def call(self, *args, **kwargs):
            self.attempts += 1
            raise ValueError("Simulated Actor Error")

    class MockClient:
        def __init__(self):
            self.mock_actor = MockActor()
            
        def actor(self, *args, **kwargs):
            return self.mock_actor

    mock_client = MockClient()
    
    try:
        await scrape_google_maps({}, client=mock_client)
    except ValueError:
        pass
        
    assert mock_client.mock_actor.attempts == 3 # 1 initial + 2 retries
    print("[x] Apify source functions handle failure by retrying 2x.")
    
    # run_hunter should swallow the error
    import app.modules.hunter.run as run_mod
    run_mod.scrape_google_maps = lambda *args, **kwargs: scrape_google_maps(*args, client=mock_client, **kwargs)
    
    state = {"csv_path": None}
    # We need to temporarily set the config to not use paid scraper to isolate the test
    # Actually it's fine, it will just fail silently as expected.
    res = await run_mod.run_hunter(state)
    assert res["hunter_inserted"] == 0
    print("[x] run_hunter catches source errors and continues.")
    print("\nALL PHASE 3 ACCEPTANCE CRITERIA MET.")

async def main():
    await verify()
    await verify_retry()
    
if __name__ == "__main__":
    asyncio.run(main())
