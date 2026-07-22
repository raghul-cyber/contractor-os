import asyncio
import os
import sys
from dotenv import load_dotenv

# MUST be first
load_dotenv(override=True)

sys.path.insert(0, os.path.abspath('.'))

from app.modules.hunter.run import run_hunter
from app.modules.profiler.run import run_profiler
from app.modules.craft.run import run_craft
from app.modules.outreach.run import run_outreach

async def main():
    print("Starting full forced cycle...")
    state = {}
    print("Running Hunter...")
    state = await run_hunter(state)
    print("Running Profiler...")
    state = await run_profiler(state)
    print("Running Craft...")
    state = await run_craft(state)
    print("Running Outreach...")
    state = await run_outreach(state)
    print("Cycle complete!")

if __name__ == "__main__":
    asyncio.run(main())
