"""Playwright scraper — primary engine for JS-heavy sites."""
import asyncio
import json
import logging
import re
from typing import Any

from scrapers.base import (
    BaseScraper, ScraperResult,
    random_delay, random_profile, random_headers, human_scroll,
    build_stealth_js, proxy_pool, retry,
)
from sites import get_site

logger = logging.getLogger(__name__)

# Max attempts for a full browser run before giving up
_MAX_ATTEMPTS = 2


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
        profile = random_profile()

        # Auto-refresh proxy pool if empty or stale (non-blocking — best effort)
        if proxy_pool.needs_refresh():
            try:
                await asyncio.wait_for(proxy_pool.refresh(max_validate=15), timeout=60)
            except asyncio.TimeoutError:
                logger.warning("[playwright] Proxy refresh timed out — proceeding without proxy")
            except Exception as exc:
                logger.warning(f"[playwright] Proxy refresh error: {exc}")

        proxy = proxy_pool.get()

        async with async_playwright() as p:
            launch_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-notifications",
                "--disable-popup-blocking",
                f"--window-size={profile.viewport['width']},{profile.viewport['height']}",
                # Randomise language flags to match profile
                f"--lang={profile.locale}",
            ]

            browser_kwargs: dict = {"headless": True, "args": launch_args}
            if proxy:
                browser_kwargs["proxy"] = {"server": proxy}
                logger.info(f"[playwright] Using proxy {proxy[:30]}…")

            browser = await p.chromium.launch(**browser_kwargs)

            context_kwargs: dict = {
                "user_agent": profile.user_agent,
                "locale": profile.locale,
                "timezone_id": profile.timezone,
                "viewport": profile.viewport,
                "is_mobile": profile.is_mobile,
                "extra_http_headers": {
                    "Accept-Language":      "en-GB,en;q=0.9",
                    "Accept-Encoding":      "gzip, deflate, br",
                    "Sec-Ch-Ua":            profile.sec_ch_ua,
                    "Sec-Ch-Ua-Mobile":     profile.sec_ch_ua_mobile,
                    "Sec-Ch-Ua-Platform":   profile.sec_ch_ua_platform,
                },
            }
            context = await browser.new_context(**context_kwargs)

            # ── Stealth patches ──────────────────────────────────────────────
            # 1. playwright-stealth package (handles ~20 known detection vectors)
            try:
                from playwright_stealth import stealth_async  # type: ignore
                await stealth_async(await context.new_page())
                # Re-create page — stealth_async patches the context via a page
                # so we close the temp page and open the real one below
                pass
            except ImportError:
                logger.debug("[playwright] playwright-stealth not available — using manual patches only")

            # 2. Our own comprehensive init script (applied to every new page)
            await context.add_init_script(build_stealth_js(profile))

            page = await context.new_page()

            # Apply stealth_async directly to page if available (belt + braces)
            try:
                from playwright_stealth import stealth_async  # type: ignore
                await stealth_async(page)
            except ImportError:
                pass

            try:
                return await self._scrape_with_retry(page, site, url, site_mod, proxy)
            finally:
                await browser.close()

    async def _scrape_with_retry(self, page, site: str, url: str, site_mod, proxy: str | None) -> list[dict[str, Any]]:
        """Navigate and scrape, retrying once with a new proxy if blocked."""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await self._navigate_and_scrape(page, site, url, site_mod)
            except _BlockedError as exc:
                if attempt < _MAX_ATTEMPTS:
                    new_proxy = proxy_pool.get()
                    if proxy:
                        proxy_pool.mark_failed(proxy)
                    logger.warning(f"[playwright/{site}] Blocked ({exc}), attempt {attempt}/{_MAX_ATTEMPTS}")
                    await random_delay(3, 7)
                    # Can't change proxy mid-session — the outer retry in scrape() will
                    # create a fresh browser with a new proxy on the next job run.
                    raise
                raise
        raise RuntimeError("exhausted retries")  # never reached but satisfies type checker

    async def _navigate_and_scrape(self, page, site: str, url: str, site_mod) -> list[dict[str, Any]]:
        # Site-specific warm-up to acquire cookies before hitting the search URL
        if site == "ebay":
            logger.info("[playwright/ebay] Warming up session via homepage")
            try:
                await page.goto("https://www.ebay.co.uk/", wait_until="domcontentloaded", timeout=20000)
                await random_delay(2, 4)
                await human_scroll(page, steps=2)
            except Exception as e:
                logger.debug(f"[playwright/ebay] homepage warmup failed: {e}")

        elif site == "vinted":
            # Hit the root first to get the consent/session cookie
            try:
                await page.goto("https://www.vinted.co.uk/", wait_until="domcontentloaded", timeout=20000)
                await random_delay(1, 3)
            except Exception as e:
                logger.debug(f"[playwright/vinted] homepage warmup failed: {e}")

        logger.info(f"[playwright] Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await random_delay(2, 5)
        await human_scroll(page, steps=random.randint(2, 4))

        # Bot challenge detection
        await self._check_blocked(page, site)

        if site == "vinted":
            return await self._scrape_vinted(page, site_mod)
        elif site == "ebay":
            return await self._scrape_ebay(page, site_mod)
        elif site == "depop":
            return await self._scrape_depop(page, site_mod)
        else:
            raise ValueError(f"Unknown site: {site}")

    async def _check_blocked(self, page, site: str):
        final_url = page.url
        block_signals = ("splashui", "/challenge", "captcha", "robot", "blocked", "verify")
        if any(k in final_url.lower() for k in block_signals):
            raise _BlockedError(f"redirect to {final_url}")
        # Check page title / body for block phrases
        try:
            title = (await page.title()).lower()
            if any(k in title for k in ("robot", "captcha", "access denied", "blocked", "challenge")):
                raise _BlockedError(f"page title: {title!r}")
        except _BlockedError:
            raise
        except Exception:
            pass

    # ─── Vinted ──────────────────────────────────────────────────────────────

    async def _scrape_vinted(self, page, site_mod) -> list[dict[str, Any]]:
        sel = site_mod.SELECTORS

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

        # ── FALLBACK: DOM scraping ─────────────────────────────────────────────
        await human_scroll(page, steps=3)

        items = await page.query_selector_all(sel["grid_item"])
        listings = []
        for item in items:
            try:
                raw = {}
                image_el    = await item.query_selector(sel["image"])
                link_el     = await item.query_selector(sel["link"])
                price_el    = await item.query_selector(sel["price"])
                subtitle_el = await item.query_selector(sel["subtitle"])

                alt_text = await image_el.get_attribute("alt") if image_el else ""
                if ", brand:" in alt_text:
                    raw["title"] = alt_text.split(", brand:")[0].strip()
                elif "," in alt_text:
                    raw["title"] = alt_text.split(",")[0].strip()
                else:
                    raw["title"] = alt_text.strip()

                raw["price"] = (await price_el.inner_text()).strip() if price_el else ""

                subtitle_text = (await subtitle_el.inner_text()).strip() if subtitle_el else ""
                parts = subtitle_text.split(" · ")
                raw["size"]      = parts[0].strip() if len(parts) > 0 else ""
                raw["condition"] = parts[1].strip() if len(parts) > 1 else ""

                raw["image_url"] = await image_el.get_attribute("src") if image_el else ""
                raw["url"]       = await link_el.get_attribute("href") if link_el else ""
                if raw["url"] and not raw["url"].startswith("http"):
                    raw["url"] = "https://www.vinted.co.uk" + raw["url"]

                if raw["url"]:
                    last_seg = raw["url"].rstrip("/").split("/")[-1]
                    raw["listing_id"] = "".join(ch for ch in last_seg if ch.isdigit())

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[playwright/vinted] DOM item parse error: {e}")

        logger.info(f"[playwright/vinted] DOM fallback found {len(listings)} listings")
        return listings

    # ─── eBay ─────────────────────────────────────────────────────────────────

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
                title_el     = await item.query_selector(sel["title"])    if sel.get("title")     else None
                price_el     = await item.query_selector(sel["price"])    if sel.get("price")     else None
                condition_el = await item.query_selector(sel["condition"]) if sel.get("condition") else None
                link_el      = await item.query_selector(sel["link"])     if sel.get("link")      else None
                image_el     = await item.query_selector(sel["image"])    if sel.get("image")     else None
                seller_sel   = sel.get("seller")
                seller_el    = await item.query_selector(seller_sel) if seller_sel else None

                raw["title"]     = (await title_el.inner_text()).strip()     if title_el     else ""
                raw["price"]     = (await price_el.inner_text()).strip()     if price_el     else ""
                raw["condition"] = (await condition_el.inner_text()).strip() if condition_el else ""
                raw["url"]       = await link_el.get_attribute("href")       if link_el      else ""
                raw["image_url"] = await image_el.get_attribute("src")       if image_el     else ""
                raw["seller"]    = (await seller_el.inner_text()).strip()    if seller_el    else ""

                url = raw.get("url", "")
                if not url or "ebay.co.uk/itm/" not in url:
                    continue

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[playwright/ebay] item parse error: {e}")

        logger.info(f"[playwright/ebay] found {len(listings)} listings")
        return listings

    # ─── Depop two-phase scraper ──────────────────────────────────────────────

    async def _scrape_depop(self, page, site_mod) -> list[dict[str, Any]]:
        try:
            await page.wait_for_selector("ol li a[href*='/products/']", timeout=15000)
        except Exception:
            logger.warning("[playwright/depop] product link selector timeout — possibly blocked")
            return []

        await random_delay(1, 2)

        items_basic = await page.evaluate("""() => {
            const items = [];
            document.querySelectorAll('ol li').forEach(li => {
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

        context = page.context
        sem = asyncio.Semaphore(4)

        async def _fetch_one(item):
            async with sem:
                prod_page = await context.new_page()
                try:
                    await stealth_if_available(prod_page)
                    await prod_page.goto(item["url"], wait_until="domcontentloaded", timeout=20000)
                    ld = await prod_page.evaluate("""() => {
                        const el = document.querySelector('script[type="application/ld+json"]');
                        if (!el) return null;
                        try { return JSON.parse(el.textContent); } catch(e) { return null; }
                    }""")
                    if not ld:
                        return item
                    offers = ld.get("offers") or {}
                    brand  = ld.get("brand")  or {}
                    images = ld.get("image")  or []
                    return {
                        "url":       item["url"],
                        "title":     ld.get("name", "").strip(),
                        "description": ld.get("description", "").strip(),
                        "price":     offers.get("price") or item.get("price", ""),
                        "currency":  offers.get("priceCurrency", "GBP"),
                        "brand":     (brand.get("name") if isinstance(brand, dict) else brand) or item.get("brand", ""),
                        "image_url": images[0] if images else item.get("image_url", ""),
                        "condition": offers.get("itemCondition", ""),
                        "size":      item.get("size", ""),
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


# ─── Helpers ─────────────────────────────────────────────────────────────────

import random  # noqa: E402 (needed for human_scroll calls above)


async def stealth_if_available(page):
    try:
        from playwright_stealth import stealth_async  # type: ignore
        await stealth_async(page)
    except ImportError:
        pass


class _BlockedError(RuntimeError):
    pass
