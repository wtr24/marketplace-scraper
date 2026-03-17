"""BeautifulSoup + requests scraper — best for static / semi-static pages (eBay)."""
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, ScraperResult, random_delay, random_headers, random_user_agent
from sites import get_site

logger = logging.getLogger(__name__)

# Headers that help pass eBay's bot check
EBAY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.co.uk/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

DEPOP_API_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-GB,en;q=0.9",
    "depop-country-code": "GB",
    "depop-currency-code": "GBP",
    "Origin": "https://www.depop.com",
    "Referer": "https://www.depop.com/",
}


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

        # Depop: requires a real browser (JS-rendered, 403 on raw HTTP).
        # Use the Playwright scraper for Depop instead.
        if site == "depop":
            raise RuntimeError("Depop requires Playwright (JS-rendered, raw HTTP 403). Use playwright engine.")

        url = site_mod.build_url(search_term)
        headers = EBAY_HEADERS if site == "ebay" else random_headers()
        await random_delay(2, 6)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers=headers,
        ) as client:
            # eBay: warm up session with a homepage hit to get cookies
            if site == "ebay":
                try:
                    await client.get("https://www.ebay.co.uk/", timeout=10)
                    await random_delay(1, 2)
                except Exception:
                    pass

            logger.info(f"[bs4] Fetching {url}")
            response = await client.get(url)
            response.raise_for_status()

            # Detect bot-challenge redirect (eBay returns 200 on the challenge page)
            final_url = str(response.url)
            if "splashui" in final_url or "/challenge" in final_url or "bot" in final_url:
                raise RuntimeError(f"Bot challenge detected — redirected to {final_url}")

        html = response.text
        soup = BeautifulSoup(html, "lxml")

        if site == "vinted":
            return self._parse_vinted(soup, site_mod)
        elif site == "ebay":
            return self._parse_ebay(soup, site_mod, html)
        else:
            raise ValueError(f"Unknown site: {site}")

    # ─── Depop JSON API ─────────────────────────────────────────────────────

    async def _fetch_depop_api(self, search_term: str, site_mod) -> list[dict[str, Any]]:
        from urllib.parse import quote_plus
        url = f"https://webapi.depop.com/api/v2/search/products/?q={quote_plus(search_term)}&sort=newlyListed&limit=48&country=gb&currency=GBP"

        headers = {
            **DEPOP_API_HEADERS,
            "User-Agent": random_user_agent(),
        }
        await random_delay(1, 3)

        async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=headers) as client:
            logger.info(f"[bs4/depop] Fetching API: {url}")
            response = await client.get(url)
            response.raise_for_status()

        data = response.json()
        products = data.get("products", [])
        listings = [site_mod.normalise(p) for p in products]
        logger.info(f"[bs4/depop] API returned {len(listings)} listings")
        return listings

    # ─── eBay (primary BS4 target — partially static) ───────────────────────

    def _parse_ebay(self, soup: BeautifulSoup, site_mod, html: str) -> list[dict[str, Any]]:
        # Sanity-check: if challenge page served, the results list won't exist
        sel = site_mod.SELECTORS
        result_list = soup.select_one(sel["result_list"])
        if not result_list:
            # Check if it looks like a bot-challenge page
            if "challenge" in html.lower() or "robot" in html.lower() or soup.select_one("li.s-item") is None:
                raise RuntimeError("eBay returned no results list — likely bot-challenge or CAPTCHA page")

        items = soup.select(sel["item"])
        listings = []

        for item in items:
            try:
                raw = {}
                title_el   = item.select_one(sel["title"])
                price_el   = item.select_one(sel["price"])
                condition_el = item.select_one(sel["condition"])
                link_el    = item.select_one(sel["link"])
                image_el   = item.select_one(sel["image"])

                raw["title"]     = title_el.get_text(strip=True) if title_el else ""
                raw["price"]     = price_el.get_text(strip=True) if price_el else ""
                raw["condition"] = condition_el.get_text(strip=True) if condition_el else ""
                raw["url"]       = link_el.get("href", "") if link_el else ""
                # eBay lazy-loads images — real URL is in data-src, not src
                raw["image_url"] = (
                    image_el.get("data-src") or image_el.get("src", "")
                ) if image_el else ""

                # Skip ghost / promoted items
                if not raw["title"] or "Shop on eBay" in raw["title"]:
                    continue

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/ebay] parse error: {e}")

        logger.info(f"[bs4/ebay] found {len(listings)} listings")
        return listings

    # ─── Vinted (may fail — JS rendered) ────────────────────────────────────

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
                # Find the first anchor that looks like an item link
                link_el  = item.select_one("a[href*='/items/']")
                image_el = item.select_one("img")

                raw["title"]     = title_el.get_text(strip=True) if title_el else ""
                raw["price"]     = price_el.get_text(strip=True) if price_el else ""
                raw["url"]       = link_el.get("href", "") if link_el else ""
                raw["image_url"] = image_el.get("src", "") if image_el else ""

                if raw["url"] and not raw["url"].startswith("http"):
                    raw["url"] = "https://www.vinted.co.uk" + raw["url"]

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/vinted] parse error: {e}")

        logger.info(f"[bs4/vinted] found {len(listings)} listings")
        return listings
