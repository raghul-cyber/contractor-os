import asyncio
import sys
import os
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import get_session
from app.core.models import Lead

async def seed_leads():
    print("Seeding test leads...")
    async with get_session() as session:
        from sqlalchemy import select
        for data in [
            {"company_name": "Vercel", "domain": "vercel.com", "website": "https://vercel.com", "status": "RAW", "source": "test_seed_1", "email": "ahilightfreelance@gmail.com"},
            {"company_name": "OpenAI", "domain": "openai.com", "website": "https://openai.com", "status": "RAW", "source": "test_seed_2", "email": "ahilightfreelance@gmail.com"},
            {"company_name": "Stripe", "domain": "stripe.com", "website": "https://stripe.com", "status": "RAW", "source": "test_seed_3", "email": "ahilightfreelance@gmail.com"}
        ]:
            res = await session.execute(select(Lead).where(Lead.domain == data["domain"]))
            existing = res.scalars().first()
            if existing:
                existing.status = "RAW"
                # To guarantee they pass the fit score in profiler, let's force fit_score later, or rely on them being real companies.
                # Actually, the user's config requires min_fit_score > 0.0. Real tech companies should score > 0.
            else:
                session.add(Lead(**data))
        
        await session.commit()
        print("Successfully injected test leads.")
        print(f"Successfully injected 3 test leads to demonstrate ahixlight.com emails.")

if __name__ == "__main__":
    asyncio.run(seed_leads())
