import httpx
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

async def test():
    key = os.getenv("GROQ_API_KEY")
    print(f"Key: {key[:5]}...")
    async with httpx.AsyncClient() as client:
        res = await client.get('https://api.groq.com/openai/v1/models', headers={'Authorization': f'Bearer {key}'})
        print(res.status_code)
        print(res.text)

asyncio.run(test())
