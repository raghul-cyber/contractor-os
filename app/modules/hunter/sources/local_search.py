import asyncio
import time
import random
from duckduckgo_search import DDGS
from app.core.logger import get_logger

logger = get_logger(__name__)

async def scrape_local_search(filters: dict) -> list:
    """
    A robust, free fallback lead generation tool using DuckDuckGo search.
    It takes the target sectors and location, and searches for businesses matching them.
    """
    sectors = filters.get("sectors", [])
    location = filters.get("location", "Global")
    limit_per_query = filters.get("limit", 5)
    
    if not sectors:
        return []

    results = []
    seen_domains = set()

    def do_search():
        local_results = []
        with DDGS() as ddgs:
            for sector in sectors:
                # Broaden the query and restrict to top-level domains for better business hits
                query = f'{sector} (site:.com OR site:.io OR site:.co)'
                logger.info(f"Local Search Fallback querying: {query}")
                
                try:
                    ddg_results = ddgs.text(query, max_results=limit_per_query)
                    for r in ddg_results:
                        href = r.get("href", "")
                        title = r.get("title", "")
                        
                        if not href or href in seen_domains:
                            continue
                            
                        # Basic domain extraction
                        domain = href.split("/")[2] if "//" in href else href
                        if domain in seen_domains:
                            continue
                            
                        seen_domains.add(domain)
                        seen_domains.add(href)
                        
                        # Ignore massive directories that aren't real leads
                        if any(x in domain.lower() for x in ["linkedin", "facebook", "twitter", "yelp", "clutch", "g2", "capterra", "yellowpages"]):
                            continue
                            
                        local_results.append({
                            "company_name": title.split("-")[0].strip() if "-" in title else title,
                            "website": href,
                            "location": location,
                            "source": "duckduckgo_fallback"
                        })
                except Exception as e:
                    logger.error(f"Local search failed for query '{query}': {e}")
                
                # Add a random delay to prevent rate limiting from DDG
                delay = random.uniform(2.5, 5.5)
                logger.debug(f"Sleeping for {delay:.2f} seconds before next search...")
                time.sleep(delay)
                
        return local_results

    # Run the synchronous search in a thread to prevent blocking the async loop
    results = await asyncio.to_thread(do_search)
    return results
