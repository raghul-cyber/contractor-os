import os
from apify_client import ApifyClientAsync
from .utils import run_with_retry

async def scrape_google_maps(filters: dict, client: ApifyClientAsync = None) -> list:
    if not client:
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN not set")
        client = ApifyClientAsync(token)
        
    actor_id = "compass/google-maps-scraper"
    run_input = {
        "searchStringsArray": filters.get("sectors", []),
        "locationQuery": filters.get("location", ""),
        "maxCrawledPlacesPerSearch": filters.get("limit", 20)
    }
    
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
        # Apify items structure can vary depending on exact actor inputs,
        # but compass/google-maps-scraper generally yields these:
        results.append({
            "company_name": item.get("title"),
            "website": item.get("website"),
            "phone": item.get("phoneUnformatted", item.get("phone")),
            "location": item.get("city") or item.get("address"),
            "industry": item.get("categoryName"),
            "source": "apify_maps"
        })
    return results
