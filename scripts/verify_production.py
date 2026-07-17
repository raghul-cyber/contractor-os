import asyncio
from app.core.db import get_session
from app.modules.crm.digest import run_daily_digest

async def main():
    async with get_session() as s:
        d = await run_daily_digest(s)
        print(d)

if __name__ == "__main__":
    asyncio.run(main())
