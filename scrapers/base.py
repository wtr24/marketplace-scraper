"""Abstract base class for all scraper engines."""
import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 10+ user agents to rotate
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/110.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

ACCEPT_LANGUAGES = [
    "en-GB,en;q=0.9",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.9,en-GB;q=0.8",
    "en-GB,en-US;q=0.7,en;q=0.3",
]

COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_headers() -> dict[str, str]:
    return {
        **COMMON_HEADERS,
        "User-Agent": random_user_agent(),
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
    }


async def random_delay(min_s: float = 2.0, max_s: float = 8.0):
    """Sleep for a random interval to mimic human behaviour."""
    delay = random.uniform(min_s, max_s)
    logger.debug(f"Sleeping {delay:.1f}s")
    await asyncio.sleep(delay)


class ScraperResult:
    """Returned by each engine.scrape() call."""

    def __init__(
        self,
        listings: list[dict[str, Any]],
        duration_ms: int,
        success: bool,
        error_message: Optional[str] = None,
        memory_mb: Optional[float] = None,
    ):
        self.listings = listings
        self.duration_ms = duration_ms
        self.success = success
        self.error_message = error_message
        self.memory_mb = memory_mb


class BaseScraper(ABC):
    """Abstract base — all five engines implement this interface."""

    name: str = "base"

    @abstractmethod
    async def scrape(self, site: str, search_term: str) -> ScraperResult:
        """
        Scrape `site` for `search_term`.
        Returns a ScraperResult containing normalised listing dicts.

        Each listing dict must contain at minimum:
          site, listing_id, title, price, currency, listing_url
        """
        ...

    async def _measure(self, coro) -> tuple[Any, int, float]:
        """Wrap a coroutine, returning (result, duration_ms, memory_mb)."""
        import tracemalloc

        tracemalloc.start()
        start = time.monotonic()
        result = await coro
        duration_ms = int((time.monotonic() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory_mb = round(peak / 1024 / 1024, 2)
        return result, duration_ms, memory_mb
