import asyncio
from typing import Dict, Any
from urllib.parse import urljoin
from app.core.logger import get_logger
from app.core.db import get_session
from app.core.models import ActivityLog
from bs4 import BeautifulSoup
from scrapling.fetchers import Fetcher, StealthyFetcher

logger = get_logger(__name__)

def _is_suspicious_response(resp) -> bool:
    """Check if the response looks blocked/empty/anti-bot-challenged."""
    if resp.status not in (200, 201, 202, 203, 204):
        return True
    
    body = resp.body
    if not body or len(body) < 500:
        return True
        
    text = resp.text.lower()
    bot_markers = [
        "cloudflare",
        "please wait while we verify",
        "checking your browser",
        "verify you are human",
        "attention required",
        "turnstile",
        "security check",
        "robot",
        "captcha"
    ]
    # Check if a couple of bot markers exist in a short text or just any
    if any(marker in text for marker in bot_markers) and len(text) < 10000:
        return True
        
    return False

def _extract_text_from_scrapling(resp) -> str:
    """Extract visible text from a Scrapling response, stripping boilerplate."""
    try:
        if not resp or not resp.body:
            return ""
            
        soup = BeautifulSoup(resp.body, "html.parser")
        
        # Remove nav, header, footer, scripts, styles
        for tag in soup(["nav", "header", "footer", "script", "style", "noscript", "aside"]):
            tag.decompose()
            
        text = soup.get_text(separator="\n")
        
        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:5000] # Limit to 5000 chars per page to save tokens
    except Exception as e:
        logger.warning(f"Failed to extract text from response: {e}")
        return ""

async def _fetch_url(url: str, use_stealth: bool = False) -> str:
    """Fetches URL using Scrapling, falling back to StealthyFetcher if needed."""
    try:
        if use_stealth:
            # We must use asyncio.to_thread if StealthyFetcher is sync, but let's check.
            # In our test, stealth_fetcher.fetch() was synchronous. We'll use asyncio.to_thread
            fetcher = StealthyFetcher()
            resp = await asyncio.to_thread(fetcher.fetch, url)
        else:
            fetcher = Fetcher()
            resp = await asyncio.to_thread(fetcher.get, url)
            
        if not use_stealth and _is_suspicious_response(resp):
            logger.info(f"Suspicious response for {url}, falling back to StealthyFetcher")
            fetcher = StealthyFetcher()
            resp = await asyncio.to_thread(fetcher.fetch, url)
            
        if _is_suspicious_response(resp):
            return ""
            
        return _extract_text_from_scrapling(resp)
    except Exception as e:
        logger.warning(f"Failed to fetch {url} (stealth={use_stealth}): {e}")
        return ""

async def scrape_website(website_url: str, lead_id: int = None) -> Dict[str, Any]:
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
        # 1. Homepage
        fetcher = Fetcher()
        resp = await asyncio.to_thread(fetcher.get, website_url)
        
        use_stealth = False
        if _is_suspicious_response(resp):
            logger.info(f"Suspicious response for {website_url}, enabling StealthyFetcher fallback")
            use_stealth = True
            
            # Log the fallback if lead_id is provided
            if lead_id:
                async with get_session() as session:
                    session.add(ActivityLog(lead_id=lead_id, actor="profiler", action=f"Stealth fallback used for {website_url}"))
                    await session.commit()
            
            stealth_fetcher = StealthyFetcher()
            resp = await asyncio.to_thread(stealth_fetcher.fetch, website_url)
            
        result["homepage"] = _extract_text_from_scrapling(resp)
        
        # 2. About
        about_url = urljoin(website_url, "/about")
        result["about"] = await _fetch_url(about_url, use_stealth)
        
        # 3. Services or Pricing
        services_url = urljoin(website_url, "/services")
        services_text = await _fetch_url(services_url, use_stealth)
        if not services_text:
            pricing_url = urljoin(website_url, "/pricing")
            services_text = await _fetch_url(pricing_url, use_stealth)
            
        result["services"] = services_text
        
    except Exception as e:
        logger.warning(f"Scrapling website scrape failed for {website_url}: {e}")
        
    return result
