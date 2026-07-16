import pytest
from app.modules.hunter.sources.apify_maps import scrape_google_maps

class MockActor:
    def __init__(self, failure_count=2):
        self.attempts = 0
        self.failure_count = failure_count

    async def call(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failure_count:
            raise TimeoutError("Simulated Apify Timeout")
        return {"defaultDatasetId": "test_dataset_123"}

class MockDataset:
    async def list_items(self):
        # Return a mock dataset structure with an `items` attribute
        class ItemsMock:
            items = [{"title": "Test Map Lead", "website": "map.com", "phoneUnformatted": "123"}]
        return ItemsMock()

class MockApifyClient:
    def __init__(self, failure_count=2):
        self.mock_actor = MockActor(failure_count)
        
    def actor(self, actor_id):
        return self.mock_actor
        
    def dataset(self, dataset_id):
        return MockDataset()

@pytest.mark.asyncio
async def test_apify_maps_retry_success():
    # 1. Test it fails 2x then succeeds on 3rd attempt
    client = MockApifyClient(failure_count=2)
    filters = {"sectors": ["Test"], "location": "Test"}
    
    # We pass our mock client to avoid hitting the network
    results = await scrape_google_maps(filters, client=client)
    
    assert client.mock_actor.attempts == 3
    assert len(results) == 1
    assert results[0]["website"] == "map.com"

@pytest.mark.asyncio
async def test_apify_maps_retry_failure():
    # 2. Test it fails 3x and raises out
    client = MockApifyClient(failure_count=5)
    filters = {"sectors": ["Test"], "location": "Test"}
    
    with pytest.raises(TimeoutError):
        await scrape_google_maps(filters, client=client)
    
    assert client.mock_actor.attempts == 3 # Should only try 3 times (1 initial + 2 retries)
