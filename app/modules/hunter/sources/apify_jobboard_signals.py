import os
from apify_client import ApifyClientAsync
from .utils import run_with_retry

async def scrape_job_boards(filters: dict, client: ApifyClientAsync = None) -> list:
    if not client:
        token = os.getenv("APIFY_API_TOKEN")
        if not token:
            raise ValueError("APIFY_API_TOKEN not set")
        client = ApifyClientAsync(token)
        
    # We use beb/google-jobs-scraper as a proxy for job board signals
    actor_id = "beb/google-jobs-scraper"
    
    # We build search queries by combining pain_signals (roles) with location
    pain_signals = filters.get("pain_signals", [])
    location = filters.get("location", "Global")
    limit_per_query = filters.get("limit", 5) # Keep it small per role
    
    if not pain_signals:
        return []
        
    search_queries = [f"{role} in {location}" for role in pain_signals]
    
    run_input = {
        "queries": "\n".join(search_queries),
        "maxResultsPerQuery": limit_per_query,
        "csvFriendly": False
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
            # Apify google jobs scraper might not reliably give website, but it gives company name.
            # Profiler will have to fill in the rest via scraping/search if needed, but we can seed it.
            "website": item.get("companyWebsite"), 
            "location": item.get("location"),
            "source": "apify_jobboard",
            # We can capture the specific role they are hiring for as context
            "decision_maker_note": f"Hiring for: {item.get('title')}"
        })
        
    return results
