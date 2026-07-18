import asyncio
from app.modules.profiler.scrapers.site_crawl import crawl_lead_site

async def test():
    results = await crawl_lead_site("example.com", max_pages=3, max_depth=1)
    print("Crawled length:", len(results))
    for res in results:
        print(res['url'], res['title'])

if __name__ == "__main__":
    asyncio.run(test())
