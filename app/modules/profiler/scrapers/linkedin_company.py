import asyncio
import re
import random
from typing import Optional, Dict, Any
from app.core.logger import get_logger
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

logger = get_logger(__name__)

def _is_login_wall(url: str, html: str) -> bool:
    """Detect if we got redirected to a login wall or auth page."""
    url_lower = url.lower()
    if "authwall" in url_lower or "login" in url_lower:
        return True
    
    html_lower = html.lower()
    # Sometimes LinkedIn serves a page that says "Sign in to LinkedIn" in the title or body
    if "sign in to linkedin" in html_lower or "join linkedin" in html_lower or "security check" in html_lower:
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
            
    # Follower count
    follower_match = re.search(r'([\d,]+)\s+followers', soup.get_text(), re.IGNORECASE)
    if follower_match:
        data["followers"] = follower_match.group(1)
        
    # Extract About description text
    # Usually in a paragraph under an "About" or "Overview" heading, or with a specific class
    about_section = soup.find(lambda tag: tag.name in ["h2", "h3"] and "About" in tag.get_text(strip=True))
    if about_section:
        about_text = []
        curr = about_section.find_next_sibling()
        while curr and curr.name not in ["h2", "h3", "dl"]:
            text = curr.get_text(separator=' ', strip=True)
            if text:
                about_text.append(text)
            curr = curr.find_next_sibling()
        if about_text:
            data["about"] = " ".join(about_text)
            
    # Fallback for about text
    if "about" not in data:
        # Often it's in a <p> with a specific class or data attribute
        about_p = soup.select_one("p.break-words")
        if about_p:
            data["about"] = about_p.get_text(separator=' ', strip=True)
            
    return data

async def read_company_page(linkedin_slug_or_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches ONLY the public, logged-out version of a company's LinkedIn about page.
    Never authenticates. Returns None if it hits a login wall or fails.
    """
    if not linkedin_slug_or_url:
        return None
        
    # Respect low request rate if profiling multiple leads in batch
    await asyncio.sleep(random.uniform(1.0, 3.0))
        
    if linkedin_slug_or_url.startswith("http"):
        url = linkedin_slug_or_url
    else:
        clean_name = re.sub(r'[^a-zA-Z0-9-]', '-', linkedin_slug_or_url.lower())
        url = f"https://www.linkedin.com/company/{clean_name}/about/"
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            
            if not response:
                logger.info(f"LinkedIn fetch failed for {url} (Empty response)")
                return None
                
            if response.status not in (200, 201, 202, 203, 204):
                logger.info(f"LinkedIn page blocked/failed for {url} (status: {response.status})")
                return None
                
            html = await page.content()
            final_url = page.url
            
            if _is_login_wall(final_url, html):
                logger.info(f"LinkedIn authwall encountered for {url} (Redirected to: {final_url})")
                return None
                
            data = _extract_linkedin_data(html)
            
            await browser.close()
            return data
            
    except Exception as e:
        logger.info(f"LinkedIn Playwright scraper failed gracefully for {url}: {e}")
        return None
