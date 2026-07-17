import os
from apify_client import ApifyClientAsync
from .utils import run_with_retry

async def scrape_crunchbase(filters: dict, client: ApifyClientAsync = None) -> list:
    if not client:
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN not set")
        client = ApifyClientAsync(token)
        
    # We use a public crunchbase scraper
    actor_id = "petr_cermak/crunchbase-scraper"
    
    sectors = filters.get("sectors", [])
    if not sectors:
        return []
        
    # Example crunchbase configuration to search for recent funding in specific sectors
    run_input = {
        "searchKeywords": ", ".join(sectors),
        "maxResults": filters.get("limit", 10),
        "scrapeCompanyFunding": True
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
        company_name = item.get("name")
        if not company_name or company_name in seen_companies:
            continue
            
        seen_companies.add(company_name)
        
        # Determine funding status note if available
        funding = item.get("lastFundingType", "Unknown")
        amount = item.get("lastFundingAmount", "")
        
        results.append({
            "company_name": company_name,
            "website": item.get("domain") or item.get("website"),
            "location": item.get("locationCity"),
            "industry": item.get("categories", [None])[0] if isinstance(item.get("categories"), list) else item.get("categories"),
            "source": "apify_crunchbase",
            "decision_maker_note": f"Recent Funding: {funding} {amount}".strip()
        })
        
    return results
