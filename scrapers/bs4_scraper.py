"""BeautifulSoup scraper — best for static / semi-static pages (eBay).

Uses curl_cffi when available for browser-grade TLS fingerprinting (JA3/JA4),
which defeats the most common HTTP-layer bot detections.  Falls back to httpx
if curl_cffi is not installed.
"""
import logging
from typing import Any

from bs4 import BeautifulSoup

from scrapers.base import (
    BaseScraper, ScraperResult,
    random_delay, random_profile, random_headers, proxy_pool, retry,
)
from sites import get_site

logger = logging.getLogger(__name__)

# Chrome version to impersonate when using curl_cffi
_CURL_IMPERSONATE = "chrome124"

# Statuses that indicate rate-limiting / block (trigger retry)
_RETRY_STATUSES = {429, 503, 403, 503}


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

        if site == "depop":
            raise RuntimeError("Depop requires Playwright (JS-rendered, raw HTTP 403). Use playwright engine.")

        url = site_mod.build_url(search_term)
        await random_delay(1, 4)

        html = await retry(
            lambda: self._fetch(url, site),
            attempts=3,
            delay=5.0,
            label=f"bs4/{site}",
        )

        soup = BeautifulSoup(html, "lxml")

        if site == "vinted":
            return self._parse_vinted(soup, site_mod)
        elif site == "ebay":
            return self._parse_ebay(soup, site_mod, html)
        else:
            raise ValueError(f"Unknown site: {site}")

    async def _fetch(self, url: str, site: str) -> str:
        """Fetch URL, trying curl_cffi first (browser TLS), then httpx."""
        proxy = proxy_pool.get()
        profile = random_profile()
        headers = random_headers(profile)

        # ── curl_cffi: full browser TLS/JA3 fingerprint ──────────────────────
        try:
            return await self._fetch_curl(url, site, profile, headers, proxy)
        except ImportError:
            logger.debug("[bs4] curl_cffi not available — using httpx")
        except Exception as exc:
            logger.warning(f"[bs4] curl_cffi fetch failed: {exc} — falling back to httpx")
            if proxy:
                proxy_pool.mark_failed(proxy)
            proxy = proxy_pool.get()  # try a different proxy for httpx

        return await self._fetch_httpx(url, site, headers, proxy)

    async def _fetch_curl(self, url: str, site: str, profile, headers: dict, proxy: str | None) -> str:
        from curl_cffi.requests import AsyncSession  # type: ignore

        kwargs: dict = {
            "impersonate": _CURL_IMPERSONATE,
            "headers": headers,
            "follow_redirects": True,
            "timeout": 30,
        }
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
            logger.info(f"[bs4/curl] Using proxy {proxy[:30]}…")

        async with AsyncSession(**kwargs) as session:
            # Warm up with homepage for eBay
            if site == "ebay":
                try:
                    await session.get("https://www.ebay.co.uk/", timeout=10)
                    await random_delay(0.5, 1.5)
                except Exception:
                    pass

            logger.info(f"[bs4/curl] Fetching {url}")
            resp = await session.get(url)

            if resp.status_code in _RETRY_STATUSES:
                if proxy:
                    proxy_pool.mark_failed(proxy)
                raise RuntimeError(f"HTTP {resp.status_code} — rate limited or blocked")

            final_url = str(resp.url)
            if any(k in final_url for k in ("splashui", "/challenge", "captcha", "robot")):
                raise RuntimeError(f"Bot challenge detected — redirected to {final_url}")

            return resp.text

    async def _fetch_httpx(self, url: str, site: str, headers: dict, proxy: str | None) -> str:
        import httpx

        client_kwargs: dict = {
            "follow_redirects": True,
            "timeout": 30,
            "headers": headers,
        }
        if proxy:
            client_kwargs["proxies"] = proxy
            logger.info(f"[bs4/httpx] Using proxy {proxy[:30]}…")

        async with httpx.AsyncClient(**client_kwargs) as client:
            if site == "ebay":
                try:
                    await client.get("https://www.ebay.co.uk/", timeout=10)
                    await random_delay(0.5, 1.5)
                except Exception:
                    pass

            logger.info(f"[bs4/httpx] Fetching {url}")
            response = await client.get(url)

            if response.status_code in _RETRY_STATUSES:
                if proxy:
                    proxy_pool.mark_failed(proxy)
                raise RuntimeError(f"HTTP {response.status_code} — rate limited or blocked")

            final_url = str(response.url)
            if any(k in final_url for k in ("splashui", "/challenge", "captcha", "robot")):
                raise RuntimeError(f"Bot challenge detected — redirected to {final_url}")

            response.raise_for_status()
            return response.text

    # ─── eBay ─────────────────────────────────────────────────────────────────

    def _parse_ebay(self, soup: BeautifulSoup, site_mod, html: str) -> list[dict[str, Any]]:
        sel = site_mod.SELECTORS
        result_list = soup.select_one(sel["result_list"])
        if not result_list:
            if "challenge" in html.lower() or "robot" in html.lower() or soup.select_one("li.s-item") is None:
                raise RuntimeError("eBay returned no results list — likely bot-challenge or CAPTCHA page")

        items = soup.select(sel["item"])
        listings = []

        for item in items:
            try:
                raw = {}
                title_el     = item.select_one(sel["title"])
                price_el     = item.select_one(sel["price"])
                condition_el = item.select_one(sel["condition"])
                link_el      = item.select_one(sel["link"])
                image_el     = item.select_one(sel["image"])

                raw["title"]     = title_el.get_text(strip=True)     if title_el     else ""
                raw["price"]     = price_el.get_text(strip=True)     if price_el     else ""
                raw["condition"] = condition_el.get_text(strip=True) if condition_el else ""
                raw["url"]       = link_el.get("href", "")           if link_el      else ""
                raw["image_url"] = (
                    image_el.get("data-src") or image_el.get("src", "")
                ) if image_el else ""

                if not raw["title"] or "Shop on eBay" in raw["title"]:
                    continue

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/ebay] parse error: {e}")

        logger.info(f"[bs4/ebay] found {len(listings)} listings")
        return listings

    # ─── Vinted (may fail — JS rendered) ─────────────────────────────────────

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
                link_el  = item.select_one("a[href*='/items/']")
                image_el = item.select_one("img")

                raw["title"]     = title_el.get_text(strip=True) if title_el else ""
                raw["price"]     = price_el.get_text(strip=True) if price_el else ""
                raw["url"]       = link_el.get("href", "")        if link_el  else ""
                raw["image_url"] = image_el.get("src", "")        if image_el else ""

                if raw["url"] and not raw["url"].startswith("http"):
                    raw["url"] = "https://www.vinted.co.uk" + raw["url"]

                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[bs4/vinted] parse error: {e}")

        logger.info(f"[bs4/vinted] found {len(listings)} listings")
        return listings
