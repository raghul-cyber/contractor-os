import pytest
from app.modules.hunter.sources.apify_jobboard_signals import scrape_job_boards
from app.modules.hunter.sources.apify_crunchbase_public import scrape_crunchbase
from app.modules.hunter.sources.directory_import import scrape_directories

class MockActor:
    def __init__(self, failure_count=0):
        self.attempts = 0
        self.failure_count = failure_count

    async def call(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failure_count:
            raise TimeoutError("Simulated Apify Timeout")
        return {"defaultDatasetId": "test_dataset_123"}

class MockDataset:
    def __init__(self, items):
        self._items = items
        
    async def list_items(self):
        class ItemsMock:
            items = self._items
        return ItemsMock()

class MockApifyClient:
    def __init__(self, failure_count=0, items=None):
        self.mock_actor = MockActor(failure_count)
        self.items = items or []
        
    def actor(self, actor_id):
        return self.mock_actor
        
    def dataset(self, dataset_id):
        return MockDataset(self.items)


@pytest.mark.asyncio
async def test_jobboard_signals_retry_success():
    items = [{"companyName": "JobCompany", "companyWebsite": "job.com", "location": "Remote", "title": "DevOps Engineer"}]
    client = MockApifyClient(failure_count=2, items=items)
    filters = {"pain_signals": ["DevOps"], "location": "Test"}
    
    results = await scrape_job_boards(filters, client=client)
    
    assert client.mock_actor.attempts == 3
    assert len(results) == 1
    assert results[0]["company_name"] == "JobCompany"
    assert results[0]["website"] == "job.com"
    assert results[0]["source"] == "apify_jobboard"
    assert "DevOps Engineer" in results[0]["decision_maker_note"]

@pytest.mark.asyncio
async def test_jobboard_signals_empty_signals():
    # If no signals are provided, it should return [] without calling apify
    client = MockApifyClient()
    filters = {"pain_signals": []}
    
    results = await scrape_job_boards(filters, client=client)
    assert len(results) == 0
    assert client.mock_actor.attempts == 0

@pytest.mark.asyncio
async def test_jobboard_signals_retry_failure():
    client = MockApifyClient(failure_count=5)
    filters = {"pain_signals": ["DevOps"]}
    
    with pytest.raises(TimeoutError):
        await scrape_job_boards(filters, client=client)
    
    assert client.mock_actor.attempts == 3

@pytest.mark.asyncio
async def test_crunchbase_public():
    items = [{"name": "FundedCo", "domain": "funded.com", "locationCity": "SF", "categories": ["SaaS"], "lastFundingType": "Series A", "lastFundingAmount": "$10M"}]
    client = MockApifyClient(failure_count=0, items=items)
    filters = {"sectors": ["SaaS"]}
    
    results = await scrape_crunchbase(filters, client=client)
    
    assert len(results) == 1
    assert results[0]["company_name"] == "FundedCo"
    assert results[0]["website"] == "funded.com"
    assert results[0]["source"] == "apify_crunchbase"
    assert "Series A" in results[0]["decision_maker_note"]
    assert "$10M" in results[0]["decision_maker_note"]

@pytest.mark.asyncio
async def test_crunchbase_retry_failure():
    client = MockApifyClient(failure_count=5)
    filters = {"sectors": ["SaaS"]}
    
    with pytest.raises(TimeoutError):
        await scrape_crunchbase(filters, client=client)
    
    assert client.mock_actor.attempts == 3

@pytest.mark.asyncio
async def test_directory_import():
    items = [{"companyName": "Agency1", "website": "ag1.com", "location": "NY", "category": "Software"}]
    client = MockApifyClient(failure_count=0, items=items)
    filters = {"sectors": ["Software"]}
    
    results = await scrape_directories(filters, client=client)
    
    assert len(results) == 1
    assert results[0]["company_name"] == "Agency1"
    assert results[0]["website"] == "ag1.com"
    assert results[0]["source"] == "apify_directory"

@pytest.mark.asyncio
async def test_directory_retry_failure():
    client = MockApifyClient(failure_count=5)
    filters = {"sectors": ["Software"]}
    
    with pytest.raises(TimeoutError):
        await scrape_directories(filters, client=client)
    
    assert client.mock_actor.attempts == 3
