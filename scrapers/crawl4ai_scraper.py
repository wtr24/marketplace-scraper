"""Crawl4AI scraper — LLM-friendly Markdown output engine.

Crawl4AI fetches pages and converts them to clean Markdown.
We then use regex / structured extraction to parse listings.
"""
import logging
import re
from typing import Any

from scrapers.base import BaseScraper, ScraperResult, random_user_agent
from sites import get_site

logger = logging.getLogger(__name__)


class Crawl4AIScraper(BaseScraper):
    name = "crawl4ai"

    async def scrape(self, site: str, search_term: str) -> ScraperResult:
        try:
            listings, duration_ms, memory_mb = await self._measure(
                self._run(site, search_term)
            )
            return ScraperResult(
                listings=listings,
                duration_ms=duration_ms,
                success=True,
                memory_mb=memory_mb,
            )
        except Exception as exc:
            logger.error(f"[crawl4ai] {site}/{search_term}: {exc}", exc_info=True)
            return ScraperResult(
                listings=[],
                duration_ms=0,
                success=False,
                error_message=str(exc),
            )

    async def _run(self, site: str, search_term: str) -> list[dict[str, Any]]:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
            from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
        except ImportError:
            raise RuntimeError(
                "crawl4ai not installed. Run: pip install crawl4ai && crawl4ai-setup"
            )

        site_mod = get_site(site)
        url = site_mod.build_url(search_term)

        browser_config = BrowserConfig(
            headless=True,
            browser_type="chromium",
            user_agent=random_user_agent(),
            extra_args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

        # Build site-specific extraction schema
        schema = self._build_schema(site, site_mod)

        extraction_strategy = JsonCssExtractionStrategy(schema, verbose=False)

        run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            extraction_strategy=extraction_strategy,
            wait_for="css:" + self._wait_selector(site, site_mod),
            delay_before_return_html=3.0,
            page_timeout=30000,
            magic=True,  # auto-handle JS / SPAs
            simulate_user=True,
            override_navigator=True,
        )

        logger.info(f"[crawl4ai] Crawling {url}")
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)

        if not result.success:
            raise RuntimeError(f"Crawl4AI failed: {result.error_message}")

        raw_items = []
        if result.extracted_content:
            try:
                import json
                raw_items = json.loads(result.extracted_content)
                if isinstance(raw_items, dict):
                    raw_items = raw_items.get("items", [raw_items])
            except Exception as e:
                logger.debug(f"[crawl4ai] JSON parse error: {e}")
                # Fallback: parse markdown
                raw_items = self._parse_markdown(result.markdown, site)

        listings = []
        for raw in raw_items:
            try:
                raw["listing_url"] = raw.pop("url", raw.get("listing_url", ""))
                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[crawl4ai] normalise error: {e}")

        logger.info(f"[crawl4ai/{site}] found {len(listings)} listings")
        return listings

    def _build_schema(self, site: str, site_mod) -> dict:
        """Build JsonCssExtractionStrategy schema for each site."""
        if site == "ebay":
            sel = site_mod.SELECTORS
            return {
                "name": "eBay Listings",
                "baseSelector": sel["item"],
                "fields": [
                    {"name": "title", "selector": sel["title"], "type": "text"},
                    {"name": "price", "selector": sel["price"], "type": "text"},
                    {"name": "condition", "selector": sel["condition"], "type": "text"},
                    {"name": "url", "selector": sel["link"], "type": "attribute", "attribute": "href"},
                    {"name": "image_url", "selector": sel["image"], "type": "attribute", "attribute": "src"},
                ],
            }
        elif site == "vinted":
            sel = site_mod.SELECTORS
            return {
                "name": "Vinted Listings",
                "baseSelector": sel["grid_item"],
                "fields": [
                    {"name": "title", "selector": sel["title"], "type": "text"},
                    {"name": "price", "selector": sel["price"], "type": "text"},
                    {"name": "url", "selector": sel["link"], "type": "attribute", "attribute": "href"},
                    {"name": "image_url", "selector": sel["image"], "type": "attribute", "attribute": "src"},
                ],
            }
        elif site == "depop":
            sel = site_mod.SELECTORS
            return {
                "name": "Depop Listings",
                "baseSelector": sel["product_card"],
                "fields": [
                    {"name": "title", "selector": sel["title"], "type": "text"},
                    {"name": "price", "selector": sel["price"], "type": "text"},
                    {"name": "url", "selector": sel["link"], "type": "attribute", "attribute": "href"},
                    {"name": "image_url", "selector": sel["image"], "type": "attribute", "attribute": "src"},
                ],
            }
        return {}

    def _wait_selector(self, site: str, site_mod) -> str:
        mapping = {
            "ebay": "li.s-item",
            "vinted": "[data-testid='grid-item']",
            "depop": "article[data-testid='product-card']",
        }
        return mapping.get(site, "body")

    def _parse_markdown(self, markdown: str, site: str) -> list[dict]:
        """Last-resort: extract price mentions from markdown."""
        if not markdown:
            return []
        # Simple heuristic: find lines with £ prices
        listings = []
        lines = markdown.split("\n")
        for line in lines:
            if "£" in line:
                price_match = re.search(r"£([\d.,]+)", line)
                listings.append({
                    "title": re.sub(r"[*_\[\]#]", "", line).strip()[:200],
                    "price": price_match.group(1).replace(",", "") if price_match else "",
                })
        return listings[:50]
