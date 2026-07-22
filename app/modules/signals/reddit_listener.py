import os
import asyncio
import logging
from datetime import datetime, timedelta
import praw

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import SignalHit
from app.core.config_loader import get_config
from app.modules.crm.transitions import notify_webhook

logger = logging.getLogger(__name__)

async def poll_reddit_signals(session: AsyncSession):
    config = get_config()
    if not config.system.signals or not config.system.signals.reddit.enabled:
        return

    reddit_config = config.system.signals.reddit
    subreddits = reddit_config.subreddits
    keywords = [kw.lower() for kw in reddit_config.keywords]
    lookback_hours = reddit_config.lookback_hours
    
    if not subreddits or not keywords:
        return

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "ContractorOS/1.0")

    if not client_id or not client_secret:
        logger.warning("REDDIT_CLIENT_ID or REDDIT_CLIENT_SECRET not set. Skipping Reddit polling.")
        return

    # Initialize PRAW (Synchronous API)
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent
    )
    # Ensure read-only mode by checking for client credentials
    if not reddit.read_only:
        logger.error("Reddit instance is not read-only. Aborting.")
        return

    # Determine cutoff time
    cutoff_time = datetime.utcnow() - timedelta(hours=lookback_hours)
    cutoff_ts = cutoff_time.timestamp()

    # Track hits in this run
    new_hits = []

    # Get max created_at per platform to avoid re-alerting if script runs often
    # But wait, looking back X hours with PRAW `new()` limits us to recent.
    # To avoid duplicates, we can just check if URL exists.
    # We will do a single query at the end or per item to check existence.
    
    # We must run PRAW in a threadpool since it's synchronous
    def fetch_reddit_data():
        hits = []
        for sub_name in subreddits:
            try:
                sub = reddit.subreddit(sub_name)
                # Check new posts
                for submission in sub.new(limit=100):
                    if submission.created_utc < cutoff_ts:
                        continue
                    
                    text_to_search = (submission.title + " " + (submission.selftext or "")).lower()
                    matched_kw = next((kw for kw in keywords if kw in text_to_search), None)
                    
                    if matched_kw:
                        hits.append({
                            "subreddit": sub_name,
                            "post_title": submission.title[:200],
                            "post_url": f"https://reddit.com{submission.permalink}",
                            "author_username": getattr(submission.author, "name", "[deleted]"),
                            "matched_keyword": matched_kw,
                            "snippet": (submission.selftext[:500] + "...") if submission.selftext else "No body text"
                        })
                
                # Check new comments
                for comment in sub.comments(limit=100):
                    if comment.created_utc < cutoff_ts:
                        continue
                    
                    text_to_search = comment.body.lower()
                    matched_kw = next((kw for kw in keywords if kw in text_to_search), None)
                    
                    if matched_kw:
                        hits.append({
                            "subreddit": sub_name,
                            "post_title": "Comment in thread",
                            "post_url": f"https://reddit.com{comment.permalink}",
                            "author_username": getattr(comment.author, "name", "[deleted]"),
                            "matched_keyword": matched_kw,
                            "snippet": comment.body[:500] + "..."
                        })
                        
            except Exception as e:
                logger.error(f"Error fetching subreddit {sub_name}: {e}")
        return hits

    logger.info(f"Polling Reddit for keywords {keywords} in {subreddits}")
    hits = await asyncio.to_thread(fetch_reddit_data)
    
    if not hits:
        logger.info("No Reddit signal hits found.")
        return

    # Filter out duplicates and save to DB
    saved_count = 0
    for hit in hits:
        # Check if url already exists
        exists = await session.execute(
            select(SignalHit).where(SignalHit.post_url == hit["post_url"])
        )
        if exists.scalar_one_or_none():
            continue
        
        new_hit = SignalHit(
            platform="reddit",
            subreddit=hit["subreddit"],
            post_title=hit["post_title"],
            post_url=hit["post_url"],
            author_username=hit["author_username"],
            matched_keyword=hit["matched_keyword"],
            snippet=hit["snippet"],
            notified=0
        )
        session.add(new_hit)
        new_hits.append(new_hit)
        saved_count += 1

    if saved_count > 0:
        await session.commit()
        logger.info(f"Saved {saved_count} new Reddit signal hits.")
        
        # Send Notification
        digest_text = f"🚨 **{saved_count} New Reddit Signals!** 🚨\n\n"
        for h in new_hits:
            digest_text += f"- **{h.subreddit}** | `{h.matched_keyword}`\n"
            digest_text += f"  👤 u/{h.author_username}\n"
            digest_text += f"  🔗 {h.post_url}\n\n"
        
        await notify_webhook(digest_text)
        
        # Mark notified
        for h in new_hits:
            h.notified = 1
        await session.commit()
