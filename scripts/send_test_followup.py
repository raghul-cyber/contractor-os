import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.outreach.sender import send_email
from app.core.config_loader import get_config
from dotenv import load_dotenv
load_dotenv()

async def send_verification():
    body = """Hi Arman,

Just wanted to follow up on my previous email.

I've continued following what you're building at Bupple, and it's exciting to see the progress you're making in creating an AI operating system for modern digital production.

If you're ever looking for an engineering partner to help build AI powered products, backend systems, cloud infrastructure, automation, or custom software, I'd love to explore how AhiXLight could support your team.

If you're open to it, I'd be happy to schedule a quick 10 minute call to learn more about your roadmap and see if there's an opportunity to work together.

https://ahixlight.com
contact@ahixlight.com"""
    
    subject = "Verification: Follow-up Email Template (FU1)"
    to_email = "ahilightfreelance@gmail.com"
    
    print(f"Sending verification email to {to_email}...")
    res = await send_email(to_email, subject, body, "ahilightfreelance@gmail.com", False)
    print("Verification email sent:", res)

if __name__ == "__main__":
    asyncio.run(send_verification())
