import asyncio
from app.modules.profiler.scrapers.linkedin_company import scrape_linkedin_company

async def main():
    print("Testing LinkedIn Company Scraper...")
    data = await scrape_linkedin_company("Microsoft", "microsoft.com")
    print(f"Data: {data}")
    
    print("\nTesting LinkedIn Company Scraper (likely blocked or small company)...")
    data2 = await scrape_linkedin_company("SomeRandomCompanyThatMightNotExist123", "none.com")
    print(f"Data2: {data2}")

if __name__ == "__main__":
    asyncio.run(main())
