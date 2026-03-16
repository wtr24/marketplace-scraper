"""Playwright scraper — primary engine for JS-heavy sites."""
import logging
import re
from typing import Any

from scrapers.base import BaseScraper, ScraperResult, random_delay, random_headers, random_user_agent
from sites import get_site

logger = logging.getLogger(__name__)


class PlaywrightScraper(BaseScraper):
    name = "playwright"

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
            logger.error(f"[playwright] {site}/{search_term}: {exc}", exc_info=True)
            return ScraperResult(
                listings=[],
                duration_ms=0,
                success=False,
                error_message=str(exc),
            )

    async def _run(self, site: str, search_term: str) -> list[dict[str, Any]]:
        from playwright.async_api import async_playwright

        site_mod = get_site(site)
        url = site_mod.build_url(search_term)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--window-size=1280,800",
                ],
            )
            context = await browser.new_context(
                user_agent=random_user_agent(),
                locale="en-GB",
                timezone_id="Europe/London",
                viewport={"width": 1280, "height": 800},
                extra_http_headers={
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                },
            )

            # Stealth: hide webdriver fingerprints
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
                window.chrome = { runtime: {} };
            """)

            page = await context.new_page()

            try:
                logger.info(f"[playwright] Navigating to {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await random_delay(2, 5)

                if site == "vinted":
                    return await self._scrape_vinted(page, site_mod)
                elif site == "ebay":
                    return await self._scrape_ebay(page, site_mod)
                elif site == "depop":
                    return await self._scrape_depop(page, site_mod)
                else:
                    raise ValueError(f"Unknown site: {site}")
            finally:
                await browser.close()

    # ─── Vinted ─────────────────────────────────────────────────────────────

    async def _scrape_vinted(self, page, site_mod) -> list[dict[str, Any]]:
        sel = site_mod.SELECTORS

        # Wait for grid items
        try:
            await page.wait_for_selector(sel["grid_item"], timeout=15000)
        except Exception:
            logger.warning("[playwright/vinted] grid_item timeout — possibly blocked")
            return []

        # Scroll to load more
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
        await random_delay(1, 3)

        items = await page.query_selector_all(sel["grid_item"])
        listings = []
        for item in items:
            try:
                raw = {}
                title_el = await item.query_selector(sel["title"])
                price_el = await item.query_selector(sel["price"])
                brand_el = await item.query_selector(sel["brand"])
                size_el = await item.query_selector(sel["size"])
                condition_el = await item.query_selector(sel["condition"])
                image_el = await item.query_selector(sel["image"])
                link_el = await item.query_selector(sel["link"])

                raw["title"] = await title_el.inner_text() if title_el else ""
                raw["price"] = await price_el.inner_text() if price_el else ""
                raw["brand"] = await brand_el.inner_text() if brand_el else ""
                raw["size"] = await size_el.inner_text() if size_el else ""
                raw["condition"] = await condition_el.inner_text() if condition_el else ""
                raw["image_url"] = await image_el.get_attribute("src") if image_el else ""
                raw["url"] = await link_el.get_attribute("href") if link_el else ""

                if raw["url"] and not raw["url"].startswith("http"):
                    raw["url"] = "https://www.vinted.co.uk" + raw["url"]

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[playwright/vinted] item parse error: {e}")

        logger.info(f"[playwright/vinted] found {len(listings)} listings")
        return listings

    # ─── eBay ────────────────────────────────────────────────────────────────

    async def _scrape_ebay(self, page, site_mod) -> list[dict[str, Any]]:
        sel = site_mod.SELECTORS

        try:
            await page.wait_for_selector(sel["item"], timeout=15000)
        except Exception:
            logger.warning("[playwright/ebay] item selector timeout")
            return []

        items = await page.query_selector_all(sel["item"])
        listings = []
        for item in items:
            try:
                raw = {}
                title_el = await item.query_selector(sel["title"])
                price_el = await item.query_selector(sel["price"])
                condition_el = await item.query_selector(sel["condition"])
                link_el = await item.query_selector(sel["link"])
                image_el = await item.query_selector(sel["image"])
                seller_el = await item.query_selector(sel["seller"])

                raw["title"] = await title_el.inner_text() if title_el else ""
                raw["price"] = await price_el.inner_text() if price_el else ""
                raw["condition"] = await condition_el.inner_text() if condition_el else ""
                raw["url"] = await link_el.get_attribute("href") if link_el else ""
                raw["image_url"] = await image_el.get_attribute("src") if image_el else ""
                raw["seller"] = await seller_el.inner_text() if seller_el else ""

                # Skip promoted / sponsored items with no real URL
                if not raw.get("url") or "rover.ebay" in raw.get("url", ""):
                    continue

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[playwright/ebay] item parse error: {e}")

        logger.info(f"[playwright/ebay] found {len(listings)} listings")
        return listings

    # ─── Depop ──────────────────────────────────────────────────────────────

    async def _scrape_depop(self, page, site_mod) -> list[dict[str, Any]]:
        sel = site_mod.SELECTORS

        try:
            await page.wait_for_selector(sel["product_card"], timeout=20000)
        except Exception:
            logger.warning("[playwright/depop] product_card timeout")
            return []

        # Scroll to trigger lazy loading
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, 800)")
            await random_delay(0.5, 1.5)

        items = await page.query_selector_all(sel["product_card"])
        listings = []
        for item in items:
            try:
                raw = {}
                title_el = await item.query_selector(sel["title"])
                price_el = await item.query_selector(sel["price"])
                image_el = await item.query_selector(sel["image"])
                link_el = await item.query_selector(sel["link"])
                seller_el = await item.query_selector(sel["seller"])

                raw["title"] = await title_el.inner_text() if title_el else ""
                raw["price"] = await price_el.inner_text() if price_el else ""
                raw["image_url"] = await image_el.get_attribute("src") if image_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                raw["url"] = "https://www.depop.com" + href if href and not href.startswith("http") else href
                raw["seller"] = await seller_el.inner_text() if seller_el else ""

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[playwright/depop] item parse error: {e}")

        logger.info(f"[playwright/depop] found {len(listings)} listings")
        return listings
