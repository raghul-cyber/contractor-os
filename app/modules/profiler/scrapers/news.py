import feedparser
from typing import List, Dict, Any
from urllib.parse import quote_plus
import asyncio
from app.core.logger import get_logger

logger = get_logger(__name__)

async def scrape_google_news(company_name: str) -> List[Dict[str, Any]]:
    """
    Fetches the Google News RSS feed for the company name.
    Returns a list of headline+date+link items.
    """
    if not company_name:
        return []
        
    query = quote_plus(company_name)
    url = f"https://news.google.com/rss/search?q={query}+when:90d&hl=en-US&gl=US&ceid=US:en"
    
    # feedparser is blocking, so run it in a thread
    def _fetch_feed():
        try:
            return feedparser.parse(url)
        except Exception as e:
            logger.warning(f"Failed to parse news feed for {company_name}: {e}")
            return None
            
    feed = await asyncio.to_thread(_fetch_feed)
    if not feed or getattr(feed, "bozo", False) and not hasattr(feed, "entries"):
        return []
        
    results = []
    for entry in feed.entries[:5]: # Take top 5 recent news items
        results.append({
            "headline": getattr(entry, "title", ""),
            "date": getattr(entry, "published", ""),
            "link": getattr(entry, "link", "")
        })
        
    return results
