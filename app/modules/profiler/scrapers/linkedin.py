import asyncio
import re
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright
from app.core.logger import get_logger
from bs4 import BeautifulSoup

logger = get_logger(__name__)

async def scrape_linkedin_company(company_name: str, website_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the public company page.
    Returns None if blocked, requires login, captcha, or 404s.
    No authenticated scraping.
    """
    if not company_name:
        return None
        
    # We construct a likely public URL or use a google search Dork, but for strict public URL pattern:
    # A simple approach: guess the linkedin url from company name
    clean_name = re.sub(r'[^a-zA-Z0-9-]', '-', company_name.lower())
    url = f"https://www.linkedin.com/company/{clean_name}"
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = await context.new_page()
            
            response = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            
            if not response or not response.ok or response.status in [403, 401, 429]:
                logger.info(f"LinkedIn page blocked/failed for {url} (status: {response.status if response else 'None'})")
                await browser.close()
                return None
                
            content = await page.content()
            
            # Check if it's a login/authwall page
            if "authwall" in page.url or "login" in page.url.lower():
                logger.info(f"LinkedIn authwall encountered for {url}")
                await browser.close()
                return None
                
            # Quick extraction
            soup = BeautifulSoup(content, "html.parser")
            text = soup.get_text(separator="\n").strip()
            # Clean up whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            visible_text = "\n".join(lines)[:3000]
            
            await browser.close()
            return {"public_text": visible_text, "url": url}
            
    except Exception as e:
        logger.warning(f"LinkedIn public scrape failed for {url}: {e}")
        return None
