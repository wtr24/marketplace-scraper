"""Playwright scraper — primary engine for JS-heavy sites."""
import asyncio
import json
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

                # eBay bot-challenge detection
                if site == "ebay":
                    final_url = page.url
                    if any(k in final_url for k in ("splashui", "/challenge", "bot", "captcha")):
                        raise RuntimeError(f"eBay bot challenge detected — redirected to {final_url}")

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

        # Wait for grid items (corrected selector)
        try:
            await page.wait_for_selector(sel["grid_item"], timeout=15000)
        except Exception:
            logger.warning("[playwright/vinted] grid_item timeout — possibly blocked")
            return []

        # ── PRIMARY: __NEXT_DATA__ JSON extraction ────────────────────────────
        try:
            next_data_el = await page.query_selector("script#__NEXT_DATA__")
            if next_data_el:
                raw_json = await next_data_el.inner_text()
                data = json.loads(raw_json)
                page_props = data.get("props", {}).get("pageProps", {})
                catalog = page_props.get("catalog", {})
                items_raw = catalog.get("items") or page_props.get("items")

                if items_raw and isinstance(items_raw, list):
                    listings = []
                    for item in items_raw:
                        try:
                            price_info = item.get("price", {})
                            photos = item.get("photos", [])
                            image_url = photos[0].get("url", "") if photos else ""
                            relative_url = item.get("url", "")
                            full_url = (
                                "https://www.vinted.co.uk" + relative_url
                                if relative_url and not relative_url.startswith("http")
                                else relative_url
                            )
                            raw = {
                                "listing_id": str(item.get("id", "")),
                                "title": item.get("title", ""),
                                "description": item.get("description", ""),
                                "price": price_info.get("amount", ""),
                                "size": item.get("size_title", ""),
                                "condition": item.get("status", ""),
                                "image_url": image_url,
                                "url": full_url,
                            }
                            listings.append(site_mod.normalise(raw))
                        except Exception as e:
                            logger.debug(f"[playwright/vinted] __NEXT_DATA__ item error: {e}")

                    logger.info(f"[playwright/vinted] __NEXT_DATA__ returned {len(listings)} listings")
                    return listings
        except Exception as e:
            logger.warning(f"[playwright/vinted] __NEXT_DATA__ failed: {e} — falling back to DOM")

        # ── FALLBACK: DOM scraping with corrected selectors ───────────────────
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
        await random_delay(1, 3)

        items = await page.query_selector_all(sel["grid_item"])
        listings = []
        for item in items:
            try:
                raw = {}
                image_el = await item.query_selector(sel["image"])
                link_el = await item.query_selector(sel["link"])
                price_el = await item.query_selector(sel["price"])
                subtitle_el = await item.query_selector(sel["subtitle"])

                # Title from img alt attribute (before ', brand:' marker)
                alt_text = await image_el.get_attribute("alt") if image_el else ""
                if ", brand:" in alt_text:
                    raw["title"] = alt_text.split(", brand:")[0].strip()
                elif "," in alt_text:
                    raw["title"] = alt_text.split(",")[0].strip()
                else:
                    raw["title"] = alt_text.strip()

                raw["price"] = (await price_el.inner_text()).strip() if price_el else ""

                # Subtitle: 'Size · Condition' — split on ' · '
                subtitle_text = (await subtitle_el.inner_text()).strip() if subtitle_el else ""
                parts = subtitle_text.split(" · ")
                raw["size"] = parts[0].strip() if len(parts) > 0 else ""
                raw["condition"] = parts[1].strip() if len(parts) > 1 else ""

                raw["image_url"] = await image_el.get_attribute("src") if image_el else ""
                raw["url"] = await link_el.get_attribute("href") if link_el else ""
                if raw["url"] and not raw["url"].startswith("http"):
                    raw["url"] = "https://www.vinted.co.uk" + raw["url"]

                # listing_id from leading digits of last path segment
                if raw["url"]:
                    last_seg = raw["url"].rstrip("/").split("/")[-1]
                    raw["listing_id"] = "".join(ch for ch in last_seg if ch.isdigit())

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[playwright/vinted] DOM item parse error: {e}")

        logger.info(f"[playwright/vinted] DOM fallback found {len(listings)} listings")
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

    # ─── Depop two-phase scraper ─────────────────────────────────────────────
    # Phase 1: search page → extract product URLs + basic data (price/size/brand)
    # Phase 2: visit each product page concurrently → extract ld+json (title/description/etc.)
    # Reason: Depop search results intentionally omit titles; raw HTTP requests are 403'd;
    # browser fetch is blocked by Transcend airgap.js consent layer.
    # ld+json on product pages provides reliable structured data (schema.org/Product).

    async def _scrape_depop(self, page, site_mod) -> list[dict[str, Any]]:
        # Phase 1: wait for product links and extract basic data
        try:
            await page.wait_for_selector("ul > li a[href*='/products/']", timeout=15000)
        except Exception:
            logger.warning("[playwright/depop] product link selector timeout — possibly blocked")
            return []

        await random_delay(1, 2)

        items_basic = await page.evaluate("""() => {
            const items = [];
            document.querySelectorAll('ul > li').forEach(li => {
                const link = li.querySelector('a[href*="/products/"]');
                if (!link) return;
                const paras = li.querySelectorAll('p');
                const img = li.querySelector('img');
                items.push({
                    url: link.href,
                    price: paras[0]?.textContent?.trim() || '',
                    size: paras[1]?.textContent?.trim() || '',
                    brand: paras[2]?.textContent?.trim() || '',
                    image_url: img?.src || img?.dataset?.src || '',
                });
            });
            return items.filter(x => x.url.includes('/products/'));
        }""")

        logger.info(f"[playwright/depop] Found {len(items_basic)} product links on search page")
        if not items_basic:
            return []

        # Phase 2: visit each product page concurrently and extract ld+json
        context = page.context
        sem = asyncio.Semaphore(4)

        async def _fetch_one(item):
            async with sem:
                prod_page = await context.new_page()
                try:
                    await prod_page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
                    ld = await prod_page.evaluate("""() => {
                        const el = document.querySelector('script[type="application/ld+json"]');
                        if (!el) return null;
                        try { return JSON.parse(el.textContent); } catch(e) { return null; }
                    }""")
                    if not ld:
                        logger.debug(f"[playwright/depop] no ld+json at {item['url']}, using basic data")
                        return item
                    offers = ld.get("offers") or {}
                    brand = ld.get("brand") or {}
                    images = ld.get("image") or []
                    return {
                        "url": item["url"],
                        "title": ld.get("name", "").strip(),
                        "description": ld.get("description", "").strip(),
                        "price": offers.get("price") or item.get("price", ""),
                        "currency": offers.get("priceCurrency", "GBP"),
                        "brand": (brand.get("name") if isinstance(brand, dict) else brand) or item.get("brand", ""),
                        "image_url": images[0] if images else item.get("image_url", ""),
                        "condition": offers.get("itemCondition", ""),
                        "size": item.get("size", ""),
                    }
                except Exception as e:
                    logger.debug(f"[playwright/depop] product page error {item['url']}: {e}")
                    return item
                finally:
                    await prod_page.close()

        results = await asyncio.gather(
            *[_fetch_one(i) for i in items_basic[:24]],
            return_exceptions=True,
        )

        listings = []
        for r in results:
            if isinstance(r, Exception) or not r:
                continue
            listings.append(site_mod.normalise(r))

        logger.info(f"[playwright/depop] Scraped {len(listings)} listings with full data")
        return listings
