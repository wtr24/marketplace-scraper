"""Puppeteer scraper — Node.js Chrome automation via subprocess.

Runs a Node.js script using puppeteer-extra + stealth plugin.
Results are returned as JSON via stdout.
"""
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
from typing import Any

from scrapers.base import BaseScraper, ScraperResult, random_user_agent
from sites import get_site

logger = logging.getLogger(__name__)

# Node.js script template — written to a temp file and executed
PUPPETEER_SCRIPT = """
const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());

const [url, site] = process.argv.slice(2);

const delay = (ms) => new Promise(r => setTimeout(r, ms));
const randDelay = () => delay(2000 + Math.random() * 6000);

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--disable-dev-shm-usage',
      '--window-size=1280,800',
    ],
  });

  const page = await browser.newPage();
  await page.setUserAgent(USER_AGENT);
  await page.setExtraHTTPHeaders({
    'Accept-Language': 'en-GB,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
  });
  await page.setViewport({ width: 1280, height: 800 });

  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await randDelay();

    let listings = [];

    if (site === 'vinted') {
      await page.waitForSelector('[data-testid="grid-item"]', { timeout: 15000 }).catch(() => {});
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight * 0.6));
      await delay(2000);

      listings = await page.evaluate(() => {
        return Array.from(document.querySelectorAll('[data-testid="grid-item"]')).map(el => ({
          title: el.querySelector('[data-testid="item-title"]')?.innerText || '',
          price: el.querySelector('[data-testid="item-price"]')?.innerText || '',
          brand: el.querySelector('[data-testid="item-brand"]')?.innerText || '',
          size: el.querySelector('[data-testid="item-size"]')?.innerText || '',
          condition: el.querySelector('[data-testid="item-status"]')?.innerText || '',
          image_url: el.querySelector('img')?.src || '',
          url: el.querySelector('a[data-testid="item-link"]')?.href || '',
          seller: el.querySelector('[data-testid="owner-username"]')?.innerText || '',
        }));
      });

    } else if (site === 'ebay') {
      await page.waitForSelector('li.s-item', { timeout: 15000 }).catch(() => {});

      listings = await page.evaluate(() => {
        return Array.from(document.querySelectorAll('li.s-item')).map(el => ({
          title: el.querySelector('.s-item__title')?.innerText || '',
          price: el.querySelector('.s-item__price')?.innerText || '',
          condition: el.querySelector('.s-item__subtitle .SECONDARY_INFO')?.innerText || '',
          url: el.querySelector('a.s-item__link')?.href || '',
          image_url: el.querySelector('.s-item__image-img')?.src || '',
          seller: el.querySelector('.s-item__seller-info-text')?.innerText || '',
        })).filter(i => i.title && !i.title.includes('Shop on eBay'));
      });

    } else if (site === 'depop') {
      await page.waitForSelector('article[data-testid="product-card"]', { timeout: 20000 }).catch(() => {});
      for (let i = 0; i < 3; i++) {
        await page.evaluate(() => window.scrollBy(0, 800));
        await delay(1000);
      }

      listings = await page.evaluate(() => {
        return Array.from(document.querySelectorAll('article[data-testid="product-card"]')).map(el => {
          const href = el.querySelector('a[data-testid="product-card__link"]')?.getAttribute('href') || '';
          return {
            title: el.querySelector('[data-testid="product-card__description"]')?.innerText || '',
            price: el.querySelector('[data-testid="product-card__price"]')?.innerText || '',
            image_url: el.querySelector('img')?.src || '',
            url: href.startsWith('/') ? 'https://www.depop.com' + href : href,
            seller: el.querySelector('[data-testid="product-card__seller"]')?.innerText || '',
          };
        });
      });
    }

    console.log(JSON.stringify({ success: true, listings }));
  } catch (err) {
    console.log(JSON.stringify({ success: false, error: err.message, listings: [] }));
  } finally {
    await browser.close();
  }
})();
""".replace("USER_AGENT", '"{ua}"')


class PuppeteerScraper(BaseScraper):
    name = "puppeteer"

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
            logger.error(f"[puppeteer] {site}/{search_term}: {exc}", exc_info=True)
            return ScraperResult(
                listings=[],
                duration_ms=0,
                success=False,
                error_message=str(exc),
            )

    async def _run(self, site: str, search_term: str) -> list[dict[str, Any]]:
        node_bin = shutil.which("node")
        if not node_bin:
            raise RuntimeError("Node.js not found in PATH — required for Puppeteer engine")

        site_mod = get_site(site)
        url = site_mod.build_url(search_term)
        ua = random_user_agent()

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "scrape.js")
            script_content = PUPPETEER_SCRIPT.replace('"{ua}"', json.dumps(ua))

            with open(script_path, "w") as f:
                f.write(script_content)

            # Check puppeteer-extra is available
            pkg_path = _find_node_module("puppeteer-extra")
            if not pkg_path:
                raise RuntimeError(
                    "puppeteer-extra not installed. Run: npm install -g puppeteer-extra puppeteer-extra-plugin-stealth"
                )

            logger.info(f"[puppeteer] Running script for {site}")
            env = {**os.environ, "NODE_PATH": _node_modules_path()}
            proc = await asyncio.create_subprocess_exec(
                node_bin, script_path, url, site,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=tmpdir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        output = stdout.decode().strip()
        if not output:
            raise RuntimeError(f"Puppeteer script produced no output. stderr: {stderr.decode()[-300:]}")

        data = json.loads(output)
        if not data.get("success"):
            raise RuntimeError(data.get("error", "Unknown puppeteer error"))

        raw_listings = data.get("listings", [])
        listings = []
        for raw in raw_listings:
            try:
                raw["listing_url"] = raw.pop("url", "")
                listings.append(site_mod.normalise(raw))
            except Exception as e:
                logger.debug(f"[puppeteer] normalise error: {e}")

        logger.info(f"[puppeteer/{site}] found {len(listings)} listings")
        return listings


def _find_node_module(name: str) -> bool:
    """Check if a Node module is installed globally or locally."""
    paths_to_check = []
    node_bin = shutil.which("node")
    if node_bin:
        node_prefix = os.path.dirname(os.path.dirname(node_bin))
        paths_to_check.append(os.path.join(node_prefix, "lib", "node_modules", name))
        paths_to_check.append(os.path.join(node_prefix, "node_modules", name))

    # Check common locations
    for p in ["/usr/lib/node_modules", "/usr/local/lib/node_modules"]:
        paths_to_check.append(os.path.join(p, name))

    return any(os.path.exists(p) for p in paths_to_check)


def _node_modules_path() -> str:
    """Return NODE_PATH pointing to global node_modules."""
    paths = []
    node_bin = shutil.which("node")
    if node_bin:
        node_prefix = os.path.dirname(os.path.dirname(node_bin))
        paths.append(os.path.join(node_prefix, "lib", "node_modules"))
    paths += ["/usr/lib/node_modules", "/usr/local/lib/node_modules"]
    return os.pathsep.join(p for p in paths if os.path.exists(p))
