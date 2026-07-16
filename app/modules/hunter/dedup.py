import re
from sqlalchemy import select
from app.core.models import Lead

def normalize_domain(url_or_domain: str) -> str:
    """
    Normalizes a domain or URL.
    Handles bare domains, full URLs with paths/query strings, idempotently.
    e.g., 'https://Example.com/' -> 'example.com'
    """
    if not url_or_domain:
        return ""
    
    s = str(url_or_domain).lower().strip()
    # Strip protocols
    s = re.sub(r'^https?://', '', s)
    # Strip www.
    s = re.sub(r'^www\.', '', s)
    # Strip path, query, fragment
    s = s.split('/')[0].split('?')[0].split('#')[0]
    return s

async def insert_lead_if_new(session, raw: dict) -> bool:
    """
    Normalizes raw['website'] or raw['domain'] into domain.
    Checks for existing leads row with that domain.
    Returns False without inserting if found.
    Otherwise inserts a new Lead with status='RAW' and returns True.
    """
    website = raw.get('website') or raw.get('domain') or ""
    domain = normalize_domain(website)
    
    if not domain:
        return False
        
    # Check for duplicate
    result = await session.execute(select(Lead).where(Lead.domain == domain))
    existing = result.scalars().first()
    
    if existing:
        return False
        
    # Build new lead
    new_lead = Lead(
        company_name=raw.get('company_name', domain),
        domain=domain,
        website=raw.get('website'),
        email=raw.get('email'),
        phone=raw.get('phone'),
        industry=raw.get('industry'),
        location=raw.get('location'),
        size_range=raw.get('size_range'),
        source=raw.get('source', 'unknown'),
        status='RAW'
    )
    
    session.add(new_lead)
    return True
