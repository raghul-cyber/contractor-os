import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.db import engine, get_session
from app.core.models import Lead, Base
from app.modules.profiler.run import run_profiler
from sqlalchemy import text, select

async def run_live_test():
    print("--- Verifying Phase 4 Profiler (Live) ---")
    
    # 1. Ensure we have API keys
    groq_key = os.getenv("GROQ_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    
    if not (groq_key or gemini_key):
        print("SKIP: No LLM API keys found (GROQ_API_KEY, GEMINI_API_KEY). Live test skipped.")
        return
        
    print("LLM API Key found. Running live e2e test...")
    
    async with engine.begin() as conn:
        # Clear out leads
        await conn.execute(text("DELETE FROM leads"))
        
    async with get_session() as session:
        # Insert test leads
        lead1 = Lead(company_name="Vercel", domain="vercel.com", source="live_test", status="RAW")
        lead2 = Lead(company_name="Apify", domain="apify.com", source="live_test", status="RAW")
        session.add_all([lead1, lead2])
        await session.commit()
        
    # Run profiler
    state = {"run_id": None}
    res = await run_profiler(state)
    
    print(f"Profiler finished. Processed: {res['profiler_processed']}, Successful: {res['profiler_successful']}")
    
    async with get_session() as session:
        leads_res = await session.execute(select(Lead))
        leads = leads_res.scalars().all()
        for lead in leads:
            print(f"Lead: {lead.company_name}")
            print(f"  Status: {lead.status}")
            print(f"  Fit Score: {lead.fit_score}")
            if lead.profile_json:
                print(f"  Profile length: {len(lead.profile_json)} chars")
            else:
                print("  Profile: NONE")
                
if __name__ == "__main__":
    asyncio.run(run_live_test())
