import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import Lead
from sqlalchemy import select

async def run():
    async with get_session() as session:
        # Delete existing
        companies = ["Vercel", "OpenAI", "Stripe", "Anthropic"]
        res = await session.execute(select(Lead))
        for lead in res.scalars().all():
            if lead.company_name in companies:
                await session.delete(lead)
        await session.commit()
        
        # Inject fresh RESEARCHED leads
        dummy_profile = json.dumps({
            "company_name": "Dummy",
            "tech_stack": ["React", "Python"],
            "pain_points": ["manual workflows", "technical debt"],
            "personalization_hooks": ["I saw your recent launch!"]
        })
        
        leads = [
            Lead(company_name="Vercel", domain="vercel.com", website="https://vercel.com", status="RESEARCHED", source="test", email="ahilightfreelance@gmail.com", fit_score=1.0, profile_json=dummy_profile.replace("Dummy", "Vercel")),
            Lead(company_name="OpenAI", domain="openai.com", website="https://openai.com", status="RESEARCHED", source="test", email="ahilightfreelance@gmail.com", fit_score=1.0, profile_json=dummy_profile.replace("Dummy", "OpenAI")),
            Lead(company_name="Stripe", domain="stripe.com", website="https://stripe.com", status="RESEARCHED", source="test", email="ahilightfreelance@gmail.com", fit_score=1.0, profile_json=dummy_profile.replace("Dummy", "Stripe"))
        ]
        
        session.add_all(leads)
        await session.commit()
        print("Successfully injected RESEARCHED test leads.")

if __name__ == "__main__":
    asyncio.run(run())
