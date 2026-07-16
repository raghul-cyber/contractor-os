import json
from pydantic import BaseModel, ValidationError
from typing import List, Optional, Union
from app.core.logger import get_logger

logger = get_logger(__name__)

class ProfileModel(BaseModel):
    company_name: str
    website: Optional[str] = None
    industry: Optional[str] = None
    size: Optional[str] = None
    location: Optional[str] = None
    tech_stack: List[str]
    recent_news: Union[str, List[str]]
    pain_points: List[str]
    decision_maker: Optional[str] = None
    decision_maker_email: Optional[str] = None
    decision_maker_title: Optional[str] = None
    personalization_hooks: List[str]

async def synthesize_profile(lead, scraped_website: dict, scraped_linkedin: dict, scraped_news: list, router) -> ProfileModel:
    prompt = f"""
You are an expert sales researcher. Your task is to synthesize a structured profile of a target company based on scraped data and known fields.

# Known Fields
Company Name: {lead.company_name}
Domain/Website: {lead.domain} or {lead.website}
Location: {lead.location}
Industry: {lead.industry}
Size Range: {lead.size_range}
Email: {lead.email}
Phone: {lead.phone}

# Scraped Website Data
Homepage: {scraped_website.get('homepage', '')[:1000]}
About: {scraped_website.get('about', '')[:1000]}
Services: {scraped_website.get('services', '')[:1000]}

# Scraped LinkedIn Data
{scraped_linkedin.get('public_text', '')[:1000] if scraped_linkedin else 'No LinkedIn data'}

# Scraped News
{json.dumps(scraped_news)}

# Instructions
Return ONLY valid JSON matching the following schema. No markdown formatting or extra text.

{{
    "company_name": "str",
    "website": "str or null",
    "industry": "str or null",
    "size": "str or null",
    "location": "str or null",
    "tech_stack": ["list of strings"],
    "recent_news": "str or list of strings",
    "pain_points": ["list of potential pain points inferred from their business model"],
    "decision_maker": "str or null",
    "decision_maker_email": "str or null",
    "decision_maker_title": "str or null",
    "personalization_hooks": ["list of specific hooks for cold outreach"]
}}
"""
    
    response_text = ""
    try:
        response_text = await router.call(prompt, task_type="research_synthesis")
        # Attempt to parse
        return parse_llm_json(response_text)
    except Exception as e:
        logger.warning(f"Initial synthesis failed for {lead.company_name}: {e}. Retrying with correction prompt.")
        
        # Retry ONCE
        correction_prompt = f"""
{prompt}

# Correction Note
Your last output was not valid JSON matching the schema, return ONLY valid JSON, no markdown fences, no commentary.
Previous failing output:
{response_text[:500]}
"""
        try:
            retry_text = await router.call(correction_prompt, task_type="research_synthesis")
            return parse_llm_json(retry_text)
        except Exception as retry_e:
            logger.error(f"Retry synthesis failed for {lead.company_name}: {retry_e}")
            raise ValueError(retry_text if 'retry_text' in locals() else response_text)

def parse_llm_json(text: str) -> ProfileModel:
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
        
    data = json.loads(text.strip())
    return ProfileModel(**data)
