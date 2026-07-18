import asyncio
import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.modules.profiler.scrapers.site_crawl import crawl_lead_site

async def main():
    target = "https://ahixlight.com"
    print(f"Starting live Scrapling crawl against {target} ...\n")
    
    results = await crawl_lead_site(target, max_pages=5, max_depth=2)
    
    print(f"\n--- CRAWL COMPLETE ---")
    print(f"Total pages crawled: {len(results)}\n")
    
    for i, res in enumerate(results):
        url = res.get("url")
        title = res.get("title")
        content = res.get("text_content", "")
        
        # Snippet
        snippet = content[:200].replace('\n', ' ') + "..." if len(content) > 200 else content.replace('\n', ' ')
        
        print(f"Page {i+1}:")
        print(f"  URL:   {url}")
        print(f"  Title: {title.encode('ascii', 'ignore').decode('ascii')}")
        print(f"  Body snippet: {snippet.encode('ascii', 'ignore').decode('ascii')}\n")

if __name__ == "__main__":
    asyncio.run(main())
