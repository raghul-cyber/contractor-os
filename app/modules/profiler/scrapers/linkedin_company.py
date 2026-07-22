import asyncio
import re
from typing import Optional, Dict, Any
from app.core.logger import get_logger
from bs4 import BeautifulSoup
from scrapling.fetchers import StealthyFetcher

logger = get_logger(__name__)

def _is_login_wall(url: str, html: str) -> bool:
    """Detect if we got redirected to a login wall or auth page."""
    url_lower = url.lower()
    if "authwall" in url_lower or "login" in url_lower:
        return True
    
    html_lower = html.lower()
    # Sometimes LinkedIn serves a page that says "Sign in to LinkedIn" in the title or body
    if "sign in to linkedin" in html_lower or "join linkedin" in html_lower:
        # Check if the main content is missing
        if "company-about" not in html_lower and "about us" not in html_lower:
            return True
            
    return False

def _extract_linkedin_data(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    
    data = {}
    
    # Extract Company Name
    title = soup.find("title")
    if title:
        data["company_name"] = title.get_text().split("|")[0].strip()
        
    # Extract structured data from DL/DT/DD pairs
    # LinkedIn often stores Industry, Company size, Headquarters, Website in definition lists
    dls = soup.find_all("dl")
    for dl in dls:
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        if len(dts) == len(dds):
            for dt, dd in zip(dts, dds):
                key = dt.get_text(strip=True).lower()
                val = dd.get_text(strip=True)
                
                if "website" in key:
                    data["website"] = val
                elif "industry" in key:
                    data["industry"] = val
                elif "company size" in key:
                    data["size"] = val
                elif "headquarters" in key:
                    data["headquarters"] = val
                    
    # Sometimes the data is just text. Let's try to extract employee count from raw text if not found
    if "size" not in data:
        text = soup.get_text()
        emp_match = re.search(r'([\d,]+(?:-[\d,]+)?(?:\+)?)\s*employees', text, re.IGNORECASE)
        if emp_match:
            data["size"] = emp_match.group(1)
            
    # Try to extract posts if visible (usually in 'update-components-text' or similar)
    posts = []
    for post in soup.select('.update-components-text'):
        post_text = post.get_text(separator=' ', strip=True)
        if post_text and post_text not in posts:
            posts.append(post_text)
            
    if posts:
        data["recent_posts"] = posts[:3]
        
    return data

async def scrape_linkedin_company(company_name: str, website_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches ONLY the public, logged-out version of a company's LinkedIn about page.
    Never authenticates. Returns None if it hits a login wall or fails.
    """
    if not company_name:
        return None
        
    clean_name = re.sub(r'[^a-zA-Z0-9-]', '-', company_name.lower())
    url = f"https://www.linkedin.com/company/{clean_name}/about"
    
    try:
        # Use StealthyFetcher for anti-bot handling
        fetcher = StealthyFetcher()
        resp = await asyncio.to_thread(fetcher.fetch, url)
        
        if not resp:
            logger.info(f"LinkedIn fetch failed for {url} (Empty response)")
            return None
            
        if resp.status not in (200, 201, 202, 203, 204):
            logger.info(f"LinkedIn page blocked/failed for {url} (status: {resp.status})")
            return None
            
        if _is_login_wall(resp.url, resp.text):
            logger.info(f"LinkedIn authwall encountered for {url} (Redirected to: {resp.url})")
            return None
            
        data = _extract_linkedin_data(resp.text)
        data["url"] = url
        
        # Format the public_text for the synthesizer
        summary_parts = []
        if data.get("company_name"): summary_parts.append(f"Name: {data['company_name']}")
        if data.get("industry"): summary_parts.append(f"Industry: {data['industry']}")
        if data.get("size"): summary_parts.append(f"Size: {data['size']}")
        if data.get("headquarters"): summary_parts.append(f"HQ: {data['headquarters']}")
        if data.get("website"): summary_parts.append(f"Website: {data['website']}")
        
        if data.get("recent_posts"):
            summary_parts.append("Recent Posts:")
            for p in data["recent_posts"]:
                summary_parts.append(f"- {p}")
                
        data["public_text"] = "\n".join(summary_parts)
        
        return data
            
    except Exception as e:
        logger.warning(f"LinkedIn public scrape failed for {url}: {e}")
        return None
