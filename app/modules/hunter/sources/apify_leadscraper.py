import os
from apify_client import ApifyClientAsync
from .utils import run_with_retry

async def scrape_leads(config: dict, client: ApifyClientAsync = None) -> list:
    if not client:
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN not set")
        client = ApifyClientAsync(token)
        
    actor_id = config.get("leadscraper_actor_id", "pipelinelabs/lead-scraper")
    run_input = config.get("leadscraper_input", {})
    
    async def _run():
        run = await client.actor(actor_id).call(run_input=run_input)
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            return []
        items = await client.dataset(dataset_id).list_items()
        return items.items

    items = await run_with_retry(_run)
    
    results = []
    for item in items:
        results.append({
            "company_name": item.get("companyName", item.get("name")),
            "website": item.get("website", item.get("domain")),
            "email": item.get("email"),
            "phone": item.get("phone"),
            "source": "apify_leadscraper"
        })
    return results
