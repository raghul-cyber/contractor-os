import asyncio
from typing import Dict, Any
from urllib.parse import urljoin
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from app.core.logger import get_logger
from bs4 import BeautifulSoup

logger = get_logger(__name__)

async def _extract_text(page, url: str) -> str:
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        if not response or not response.ok:
            return ""
        
        # Simple boilerplate stripping with bs4
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove nav, header, footer, scripts, styles
        for tag in soup(["nav", "header", "footer", "script", "style", "noscript", "aside"]):
            tag.decompose()
            
        text = soup.get_text(separator="\n")
        
        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:5000] # Limit to 5000 chars per page to save tokens
    except Exception as e:
        logger.warning(f"Failed to extract {url}: {e}")
        return ""

async def scrape_website(website_url: str) -> Dict[str, Any]:
    """
    Visits the lead's homepage, /about, and /services (or /pricing).
    Extracts visible text and strips boilerplate.
    Gracefully returns partial/empty data on failure.
    """
    result = {
        "homepage": "",
        "about": "",
        "services": ""
    }
    
    if not website_url:
        return result

    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            page = await context.new_page()
            
            # 1. Homepage
            result["homepage"] = await _extract_text(page, website_url)
            
            # 2. About
            about_url = urljoin(website_url, "/about")
            result["about"] = await _extract_text(page, about_url)
            
            # 3. Services or Pricing
            services_url = urljoin(website_url, "/services")
            services_text = await _extract_text(page, services_url)
            if not services_text:
                pricing_url = urljoin(website_url, "/pricing")
                services_text = await _extract_text(page, pricing_url)
                
            result["services"] = services_text
            
            await browser.close()
    except Exception as e:
        logger.warning(f"Playwright website scrape failed for {website_url}: {e}")
        
    return result
