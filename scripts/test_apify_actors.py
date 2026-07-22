import asyncio
import os
from apify_client import ApifyClientAsync

actors = [
    "epctex/clutch-scraper",
    "compass/google-maps-scraper",
    "pipelinelabs/lead-scraper",
    "beb/google-jobs-scraper",
    "petr_cermak/crunchbase-scraper",
    "vdrmota/contact-info-scraper"
]

async def test_actors():
    token = os.getenv("APIFY_API_TOKEN")
    client = ApifyClientAsync(token)
    
    for actor_id in actors:
        try:
            # just get the actor to see if it exists
            actor = await client.actor(actor_id).get()
            if actor:
                print(f"[OK] {actor_id} exists. ID: {actor.get('id')}")
            else:
                print(f"[FAIL] {actor_id} returned None")
        except Exception as e:
            print(f"[ERROR] {actor_id}: {e}")

if __name__ == "__main__":
    asyncio.run(test_actors())
