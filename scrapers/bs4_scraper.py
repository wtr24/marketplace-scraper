"""BeautifulSoup + requests scraper — best for static / semi-static pages (eBay)."""
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, ScraperResult, random_delay, random_headers
from sites import get_site

logger = logging.getLogger(__name__)


class BS4Scraper(BaseScraper):
    name = "beautifulsoup"

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
            logger.error(f"[bs4] {site}/{search_term}: {exc}", exc_info=True)
            return ScraperResult(
                listings=[],
                duration_ms=0,
                success=False,
                error_message=str(exc),
            )

    async def _run(self, site: str, search_term: str) -> list[dict[str, Any]]:
        site_mod = get_site(site)
        url = site_mod.build_url(search_term)

        headers = random_headers()
        await random_delay(2, 6)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers=headers,
        ) as client:
            logger.info(f"[bs4] Fetching {url}")
            response = await client.get(url)
            response.raise_for_status()

        html = response.text
        soup = BeautifulSoup(html, "lxml")

        if site == "vinted":
            return self._parse_vinted(soup, site_mod)
        elif site == "ebay":
            return self._parse_ebay(soup, site_mod)
        elif site == "depop":
            return self._parse_depop(soup, site_mod)
        else:
            raise ValueError(f"Unknown site: {site}")

    # ─── eBay (primary BS4 target — partially static) ───────────────────────

    def _parse_ebay(self, soup: BeautifulSoup, site_mod) -> list[dict[str, Any]]:
        sel = site_mod.SELECTORS
        items = soup.select(sel["item"])
        listings = []

        for item in items:
            try:
                raw = {}
                title_el = item.select_one(sel["title"])
                price_el = item.select_one(sel["price"])
                condition_el = item.select_one(sel["condition"])
                link_el = item.select_one(sel["link"])
                image_el = item.select_one(sel["image"])

                raw["title"] = title_el.get_text(strip=True) if title_el else ""
                raw["price"] = price_el.get_text(strip=True) if price_el else ""
                raw["condition"] = condition_el.get_text(strip=True) if condition_el else ""
                raw["url"] = link_el.get("href", "") if link_el else ""
                raw["image_url"] = image_el.get("src", "") if image_el else ""

                # Skip ghost items (no title or "Shop on eBay")
                if not raw["title"] or "Shop on eBay" in raw["title"]:
                    continue

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/ebay] parse error: {e}")

        logger.info(f"[bs4/ebay] found {len(listings)} listings")
        return listings

    # ─── Vinted (may fail — JS rendered) ───────────────────────────────────

    def _parse_vinted(self, soup: BeautifulSoup, site_mod) -> list[dict[str, Any]]:
        sel = site_mod.BS4_SELECTORS
        items = soup.select(sel["grid_item"])

        if not items:
            logger.warning("[bs4/vinted] no items found — site is JS-rendered, use Playwright")
            return []

        listings = []
        for item in items:
            try:
                raw = {}
                title_el = item.select_one(sel["title"])
                price_el = item.select_one(sel["price"])
                link_el = item.select_one("a")

                raw["title"] = title_el.get_text(strip=True) if title_el else ""
                raw["price"] = price_el.get_text(strip=True) if price_el else ""
                raw["url"] = link_el.get("href", "") if link_el else ""

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/vinted] parse error: {e}")

        logger.info(f"[bs4/vinted] found {len(listings)} listings")
        return listings

    # ─── Depop (may fail — heavy SPA) ───────────────────────────────────────

    def _parse_depop(self, soup: BeautifulSoup, site_mod) -> list[dict[str, Any]]:
        # Try Depop API instead (much more reliable)
        logger.warning("[bs4/depop] SPA site — consider using Playwright for Depop")
        items = soup.select(site_mod.SELECTORS["product_card"])

        listings = []
        for item in items:
            try:
                raw = {}
                title_el = item.select_one(site_mod.SELECTORS["title"])
                price_el = item.select_one(site_mod.SELECTORS["price"])
                link_el = item.select_one(site_mod.SELECTORS["link"])

                raw["title"] = title_el.get_text(strip=True) if title_el else ""
                raw["price"] = price_el.get_text(strip=True) if price_el else ""
                href = link_el.get("href", "") if link_el else ""
                raw["url"] = "https://www.depop.com" + href if href else ""

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/depop] parse error: {e}")

        logger.info(f"[bs4/depop] found {len(listings)} listings")
        return listings
