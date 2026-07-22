from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.db import get_session
from app.core.models import SignalHit

router = APIRouter()

@router.get("/reddit")
async def get_reddit_signals(session: AsyncSession = Depends(get_session)):
    # Get the latest 50 hits
    result = await session.execute(
        select(SignalHit)
        .where(SignalHit.platform == "reddit")
        .order_by(SignalHit.created_at.desc())
        .limit(50)
    )
    hits = result.scalars().all()
    
    return {
        "signals": [
            {
                "id": hit.id,
                "subreddit": hit.subreddit,
                "post_title": hit.post_title,
                "post_url": hit.post_url,
                "author_username": hit.author_username,
                "matched_keyword": hit.matched_keyword,
                "snippet": hit.snippet,
                "created_at": hit.created_at
            }
            for hit in hits
        ]
    }
