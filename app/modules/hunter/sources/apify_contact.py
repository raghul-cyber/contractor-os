import os
from apify_client import ApifyClientAsync
from .utils import run_with_retry

async def extract_contacts(url: str, client: ApifyClientAsync = None) -> dict:
    if not client:
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN not set")
        client = ApifyClientAsync(token)
        
    actor_id = "vdrmota/contact-info-scraper"
    
    run_input = {
        "startUrls": [{"url": url}],
        "maxDepth": 1,
        "maxPages": 5
    }
    
    async def _run():
        run = await client.actor(actor_id).call(run_input=run_input)
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            return []
        items = await client.dataset(dataset_id).list_items()
        return items.items

    items = await run_with_retry(_run)
    
    if not items:
        return {}
        
    emails = set()
    phones = set()
    for item in items:
        for email in item.get("emails", []):
            emails.add(email)
        for phone in item.get("phones", []):
            phones.add(phone)
            
    return {
        "email": list(emails)[0] if emails else None,
        "phone": list(phones)[0] if phones else None
    }
