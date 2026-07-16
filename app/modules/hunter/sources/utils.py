import asyncio
from typing import Callable, Any
from app.core.logger import get_logger

logger = get_logger(__name__)

async def run_with_retry(func: Callable, retries: int = 2, backoff: float = 2.0, *args, **kwargs) -> Any:
    """
    Wraps an async function with retry backoff.
    """
    attempt = 0
    while True:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            attempt += 1
            if attempt > retries:
                logger.error(f"Function {func.__name__} failed after {retries} retries: {e}")
                raise e
            logger.warning(f"Function {func.__name__} failed (attempt {attempt}), retrying in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff *= 2
