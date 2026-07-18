import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from app.modules.orchestrator.graph import run_full_cycle

if __name__ == "__main__":
    print("Running ContractorOS Full Cycle in foreground...")
    asyncio.run(run_full_cycle())
    print("Cycle complete.")
