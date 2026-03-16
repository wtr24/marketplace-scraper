"""Scrapy scraper — high-volume async crawling engine.

Scrapy is designed to run as a standalone process with its own event loop.
We run it in a subprocess and collect results via a temp JSON file.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import textwrap
from typing import Any

from scrapers.base import BaseScraper, ScraperResult
from sites import get_site

logger = logging.getLogger(__name__)

# Inline spider source — written to a temp file and run via subprocess
SPIDER_TEMPLATE = textwrap.dedent("""
import json, scrapy, random
from fake_useragent import UserAgent

USER_AGENTS = {user_agents}

class MarketSpider(scrapy.Spider):
    name = "market_spider"
    custom_settings = {{
        "AUTOTHROTTLE_ENABLED": True,
        "DOWNLOAD_DELAY": 3,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "ROBOTSTXT_OBEY": False,
        "LOG_LEVEL": "WARNING",
        "FEED_FORMAT": "json",
        "FEED_URI": "{output_path}",
        "DEFAULT_REQUEST_HEADERS": {{
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }},
        "USER_AGENT": random.choice(USER_AGENTS),
        "DOWNLOADER_MIDDLEWARES": {{
            "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
        }},
    }}

    def start_requests(self):
        url = "{url}"
        ua = random.choice(USER_AGENTS)
        yield scrapy.Request(url, headers={{"User-Agent": ua}}, callback=self.parse)

    def parse(self, response):
        site = "{site}"
        if site == "ebay":
            yield from self.parse_ebay(response)
        elif site == "vinted":
            yield from self.parse_vinted(response)
        elif site == "depop":
            yield from self.parse_depop(response)

    def parse_ebay(self, response):
        for item in response.css("li.s-item"):
            title = item.css(".s-item__title::text").get("").strip()
            if not title or "Shop on eBay" in title:
                continue
            yield {{
                "site": "ebay",
                "title": title,
                "price": item.css(".s-item__price::text").get("").strip(),
                "condition": item.css(".s-item__subtitle .SECONDARY_INFO::text").get("").strip(),
                "url": item.css("a.s-item__link::attr(href)").get(""),
                "image_url": item.css(".s-item__image-img::attr(src)").get(""),
                "seller": item.css(".s-item__seller-info-text::text").get("").strip(),
            }}

    def parse_vinted(self, response):
        # Vinted is JS-rendered — BS4/Scrapy gets minimal HTML
        for item in response.css("[data-testid=grid-item]"):
            yield {{
                "site": "vinted",
                "title": item.css("[data-testid=item-title]::text").get("").strip(),
                "price": item.css("[data-testid=item-price]::text").get("").strip(),
                "url": item.css("a[data-testid=item-link]::attr(href)").get(""),
                "image_url": item.css("img::attr(src)").get(""),
            }}

    def parse_depop(self, response):
        for item in response.css("article[data-testid=product-card]"):
            href = item.css("a[data-testid=product-card__link]::attr(href)").get("")
            yield {{
                "site": "depop",
                "title": item.css("[data-testid=product-card__description]::text").get("").strip(),
                "price": item.css("[data-testid=product-card__price]::text").get("").strip(),
                "url": "https://www.depop.com" + href if href else "",
                "image_url": item.css("img::attr(src)").get(""),
            }}
""")


class ScrapyScraper(BaseScraper):
    name = "scrapy"

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
            logger.error(f"[scrapy] {site}/{search_term}: {exc}", exc_info=True)
            return ScraperResult(
                listings=[],
                duration_ms=0,
                success=False,
                error_message=str(exc),
            )

    async def _run(self, site: str, search_term: str) -> list[dict[str, Any]]:
        site_mod = get_site(site)
        url = site_mod.build_url(search_term)

        from scrapers.base import USER_AGENTS

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "results.json")
            spider_path = os.path.join(tmpdir, "spider.py")

            spider_code = SPIDER_TEMPLATE.format(
                url=url,
                site=site,
                output_path=output_path.replace("\\", "/"),
                user_agents=json.dumps(USER_AGENTS),
            )

            with open(spider_path, "w") as f:
                f.write(spider_code)

            # Run scrapy via subprocess to avoid event loop conflicts
            cmd = [
                sys.executable, "-c",
                f"""
import sys, os
sys.path.insert(0, r"{tmpdir}")
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
import importlib.util

spec = importlib.util.spec_from_file_location("spider", r"{spider_path}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

process = CrawlerProcess()
process.crawl(mod.MarketSpider)
process.start()
"""
            ]

            logger.info(f"[scrapy] Running spider for {site}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if proc.returncode != 0:
                err = stderr.decode()[-500:]
                raise RuntimeError(f"Scrapy spider failed: {err}")

            # Load results
            if not os.path.exists(output_path):
                logger.warning("[scrapy] No output file produced")
                return []

            with open(output_path) as f:
                raw_items = json.load(f)

        # Normalise
        listings = []
        for raw in raw_items:
            try:
                raw["listing_url"] = raw.pop("url", "")
                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[scrapy] normalise error: {e}")

        logger.info(f"[scrapy/{site}] found {len(listings)} listings")
        return listings
