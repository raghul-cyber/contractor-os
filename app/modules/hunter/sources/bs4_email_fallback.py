import re
import asyncio
from typing import Optional
from bs4 import BeautifulSoup
from scrapling.fetchers import Fetcher
from app.core.logger import get_logger

logger = get_logger(__name__)

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

def _is_suspicious_response(resp) -> bool:
    if not resp or resp.status not in (200, 201, 202, 203, 204):
        return True
    
    body = resp.body
    if not body or len(body) < 500:
        return True
        
    text = resp.text.lower()
    bot_markers = [
        "cloudflare", "please wait while we verify", "checking your browser",
        "verify you are human", "attention required", "turnstile",
        "security check", "robot", "captcha"
    ]
    if any(marker in text for marker in bot_markers) and len(text) < 10000:
        return True
        
    return False

def parse_emails_from_html(html: str) -> Optional[str]:
    """Parse mailto links and regex emails from visible text."""
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Check mailto: links first (highest confidence)
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith('mailto:'):
            email = href[7:].split('?')[0].strip()
            if EMAIL_REGEX.match(email):
                return email
                
    # 2. Check visible text for email patterns
    for tag in soup(["script", "style", "noscript", "nav"]):
        tag.decompose()
        
    text = soup.get_text(separator=' ')
    matches = EMAIL_REGEX.findall(text)
    
    # Filter out common false positives like "example@example.com" or png files
    for match in matches:
        match_lower = match.lower()
        if "example.com" in match_lower or "domain.com" in match_lower:
            continue
        if match_lower.endswith(".png") or match_lower.endswith(".jpg"):
            continue
        return match
        
    return None

async def fast_extract_email(url: str) -> Optional[str]:
    """
    Attempts to fetch the page with a standard Fetcher and extract an email.
    If it hits anti-bot or fails to find an email, returns None.
    """
    if not url:
        return None
        
    if not url.startswith("http"):
        url = "https://" + url
        
    try:
        fetcher = Fetcher()
        resp = await asyncio.to_thread(fetcher.get, url)
        
        if _is_suspicious_response(resp):
            logger.info(f"BS4 Email Fallback: Suspicious/blocked response for {url}")
            return None
            
        email = parse_emails_from_html(resp.text)
        if email:
            logger.info(f"BS4 Email Fallback: Found email {email} on {url}")
            return email
            
    except Exception as e:
        logger.warning(f"BS4 Email Fallback failed for {url}: {e}")
        
    return None
