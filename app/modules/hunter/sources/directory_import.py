import os
from apify_client import ApifyClientAsync
from .utils import run_with_retry

async def scrape_directories(filters: dict, client: ApifyClientAsync = None) -> list:
    if not client:
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN not set")
        client = ApifyClientAsync(token)
        
    # We use epctex/clutch-scraper or similar directory scraper
    actor_id = "epctex/clutch-scraper"
    
    sectors = filters.get("sectors", [])
    if not sectors:
        return []
        
    # Example config for Clutch
    run_input = {
        "searchKeywords": ", ".join(sectors),
        "maxItems": filters.get("limit", 10)
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
    seen_companies = set()
    for item in items:
        company_name = item.get("companyName")
        if not company_name or company_name in seen_companies:
            continue
            
        seen_companies.add(company_name)
        
        results.append({
            "company_name": company_name,
            "website": item.get("website"),
            "location": item.get("location"),
            "industry": item.get("category"),
            "source": "apify_directory"
        })
        
    return results
