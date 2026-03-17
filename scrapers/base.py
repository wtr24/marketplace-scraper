"""Abstract base class + shared anti-detection utilities for all scraper engines."""
import asyncio
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── Browser Profiles ────────────────────────────────────────────────────────
# Each profile is a self-consistent fingerprint: UA, matching sec-ch-ua headers,
# platform string, and viewport.  Mixing these (e.g. Windows UA + Mac platform)
# is a common detection signal.

@dataclass
class BrowserProfile:
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    sec_ch_ua_mobile: str
    platform: str          # navigator.platform JS value
    viewport: dict         # {width, height}
    is_mobile: bool
    locale: str = "en-GB"
    timezone: str = "Europe/London"


BROWSER_PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        sec_ch_ua_mobile="?0",
        platform="Win32",
        viewport={"width": 1920, "height": 1080},
        is_mobile=False,
    ),
    BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        sec_ch_ua_mobile="?0",
        platform="Win32",
        viewport={"width": 1440, "height": 900},
        is_mobile=False,
    ),
    BrowserProfile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"macOS"',
        sec_ch_ua_mobile="?0",
        platform="MacIntel",
        viewport={"width": 1440, "height": 900},
        is_mobile=False,
    ),
    BrowserProfile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="122", "Google Chrome";v="122", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"macOS"',
        sec_ch_ua_mobile="?0",
        platform="MacIntel",
        viewport={"width": 1280, "height": 800},
        is_mobile=False,
    ),
    BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        sec_ch_ua='"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        sec_ch_ua_mobile="?0",
        platform="Win32",
        viewport={"width": 1366, "height": 768},
        is_mobile=False,
    ),
    BrowserProfile(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_platform='"Linux"',
        sec_ch_ua_mobile="?0",
        platform="Linux x86_64",
        viewport={"width": 1920, "height": 1080},
        is_mobile=False,
    ),
]


def random_profile() -> BrowserProfile:
    return random.choice(BROWSER_PROFILES)


def random_user_agent() -> str:
    # Try fake-useragent first (pulls fresh real-world UAs); fall back to static list
    try:
        from fake_useragent import UserAgent
        ua = UserAgent(browsers=["chrome"], os=["windows", "macos"])
        result = ua.random
        if result and len(result) > 20:
            return result
    except Exception:
        pass
    return random.choice(BROWSER_PROFILES).user_agent


def random_headers(profile: Optional[BrowserProfile] = None) -> dict[str, str]:
    p = profile or random_profile()
    return {
        "User-Agent": p.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": p.sec_ch_ua,
        "Sec-Ch-Ua-Mobile": p.sec_ch_ua_mobile,
        "Sec-Ch-Ua-Platform": p.sec_ch_ua_platform,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    }


# ─── Proxy Pool ───────────────────────────────────────────────────────────────

class ProxyPool:
    """Round-robin proxy rotation with automatic failure backoff.

    Configure via PROXY_URLS env var — comma-separated list:
        PROXY_URLS=http://user:pass@host1:port,http://user:pass@host2:port

    Supports http://, https://, socks5:// schemes.
    """

    _COOLDOWN_SECONDS = 300  # 5 min before a failed proxy is retried

    def __init__(self):
        raw = os.getenv("PROXY_URLS", "").strip()
        self._proxies: list[str] = [p.strip() for p in raw.split(",") if p.strip()] if raw else []
        self._index = 0
        self._failures: dict[str, float] = {}  # proxy_url → failed_at timestamp
        if self._proxies:
            logger.info(f"[proxy] Pool initialised with {len(self._proxies)} proxy/proxies")
        else:
            logger.debug("[proxy] No PROXY_URLS configured — direct connections only")

    @property
    def available(self) -> bool:
        return bool(self._proxies)

    def get(self) -> Optional[str]:
        """Return next working proxy, or None if pool is empty or all on cooldown."""
        if not self._proxies:
            return None
        now = time.monotonic()
        for _ in range(len(self._proxies)):
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            if now - self._failures.get(proxy, 0) > self._COOLDOWN_SECONDS:
                return proxy
        logger.warning("[proxy] All proxies on cooldown — using direct connection")
        return None

    def mark_failed(self, proxy: str):
        logger.warning(f"[proxy] Marking {proxy[:30]}… as failed (cooldown {self._COOLDOWN_SECONDS}s)")
        self._failures[proxy] = time.monotonic()


# Module-level singleton — imported by both scrapers
proxy_pool = ProxyPool()


# ─── Human-like Timing ───────────────────────────────────────────────────────

async def random_delay(min_s: float = 1.5, max_s: float = 5.0):
    """Gaussian-jittered delay that mimics human reaction time distribution."""
    # Gaussian centred at midpoint, clipped to [min_s, max_s]
    mid = (min_s + max_s) / 2
    sigma = (max_s - min_s) / 4
    delay = max(min_s, min(max_s, random.gauss(mid, sigma)))
    logger.debug(f"[delay] {delay:.2f}s")
    await asyncio.sleep(delay)


async def human_scroll(page, steps: int = 3):
    """Scroll the page in short increments, pausing briefly between each."""
    for _ in range(steps):
        pixels = random.randint(300, 700)
        await page.evaluate(f"window.scrollBy(0, {pixels})")
        await asyncio.sleep(random.uniform(0.3, 0.9))


# ─── Retry Helper ─────────────────────────────────────────────────────────────

async def retry(coro_fn, attempts: int = 3, delay: float = 4.0, label: str = ""):
    """Call coro_fn() up to `attempts` times, backing off on exceptions."""
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(1, attempts + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                wait = delay * (2 ** (attempt - 1)) + random.uniform(0, 2)
                logger.warning(f"[retry] {label} attempt {attempt}/{attempts} failed: {exc} — retrying in {wait:.1f}s")
                await asyncio.sleep(wait)
    raise last_exc


# ─── Stealth Init Script ──────────────────────────────────────────────────────
# Applied to every Playwright browser context.
# Patches the most common automation fingerprints that bot-detection scripts check.

def build_stealth_js(profile: BrowserProfile) -> str:
    hw_concurrency = random.choice([4, 6, 8, 12, 16])
    device_memory  = random.choice([4, 8, 16])
    # Randomise canvas noise seed so it differs per session
    noise_seed = random.randint(1, 10)

    return f"""
(function() {{
  // 1. Remove webdriver flag
  try {{
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
    delete navigator.__proto__.webdriver;
  }} catch(e) {{}}

  // 2. Realistic plugin list (Chrome has PDF/Flash stubs)
  try {{
    Object.defineProperty(navigator, 'plugins', {{
      get: () => {{
        const arr = [
          {{ name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
          {{ name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: '' }},
          {{ name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: '' }},
          {{ name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: '' }},
          {{ name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: '' }},
        ];
        arr.item = i => arr[i];
        arr.namedItem = n => arr.find(p => p.name === n) || null;
        arr.refresh = () => {{}};
        return arr;
      }}
    }});
    Object.defineProperty(navigator, 'mimeTypes', {{
      get: () => {{
        const arr = [
          {{ type: 'application/pdf', description: 'Portable Document Format', suffixes: 'pdf' }},
          {{ type: 'text/pdf', description: 'Portable Document Format', suffixes: 'pdf' }},
        ];
        arr.item = i => arr[i];
        arr.namedItem = t => arr.find(m => m.type === t) || null;
        return arr;
      }}
    }});
  }} catch(e) {{}}

  // 3. Consistent navigator properties
  try {{
    Object.defineProperty(navigator, 'languages', {{ get: () => ['en-GB', 'en'] }});
    Object.defineProperty(navigator, 'platform', {{ get: () => '{profile.platform}' }});
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {device_memory} }});
    Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => 0 }});
  }} catch(e) {{}}

  // 4. Realistic window.chrome object
  try {{
    if (!window.chrome) {{
      window.chrome = {{
        app: {{
          isInstalled: false,
          InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }},
          RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }},
          getDetails: () => {{}},
          getIsInstalled: () => false,
        }},
        runtime: {{
          OnInstalledReason: {{ CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' }},
          PlatformArch: {{ ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' }},
          PlatformOs: {{ ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' }},
          RequestUpdateCheckStatus: {{ NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }},
          connect: () => {{}},
          sendMessage: () => {{}},
        }},
        csi: () => {{}},
        loadTimes: () => ({{ firstPaintTime: 0, requestTime: Date.now() / 1000 - 1 }}),
      }};
    }}
  }} catch(e) {{}}

  // 5. Permissions API — return realistic results for common queries
  try {{
    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {{
      if (params.name === 'notifications') {{
        return Promise.resolve({{ state: Notification.permission, onchange: null }});
      }}
      if (['geolocation', 'camera', 'microphone'].includes(params.name)) {{
        return Promise.resolve({{ state: 'prompt', onchange: null }});
      }}
      return _origQuery(params);
    }};
  }} catch(e) {{}}

  // 6. WebGL — spoof renderer strings so GPU fingerprint looks like a real desktop
  try {{
    const _getCtx = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {{
      const ctx = _getCtx.call(this, type, ...args);
      if (!ctx) return ctx;
      if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {{
        const _getParam = ctx.getParameter.bind(ctx);
        ctx.getParameter = function(param) {{
          if (param === 37445) return 'Intel Inc.';
          if (param === 37446) return 'Intel Iris OpenGL Engine';
          return _getParam(param);
        }};
      }}
      return ctx;
    }};
  }} catch(e) {{}}

  // 7. Canvas fingerprint noise — alter pixel values by a tiny fixed amount
  // so the fingerprint differs from headless baseline without being perceptible
  try {{
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {{
      const ctx2 = this.getContext('2d');
      if (ctx2) {{
        const imageData = ctx2.getImageData(0, 0, this.width || 1, this.height || 1);
        for (let i = 0; i < imageData.data.length; i += 4 * {noise_seed * 17 + 3}) {{
          imageData.data[i] = (imageData.data[i] + {noise_seed}) & 0xff;
        }}
        ctx2.putImageData(imageData, 0, 0);
      }}
      return _toDataURL.call(this, type, ...args);
    }};
  }} catch(e) {{}}

  // 8. Window dimensions — match reported viewport
  try {{
    Object.defineProperty(window, 'outerWidth',  {{ get: () => {profile.viewport['width']} }});
    Object.defineProperty(window, 'outerHeight', {{ get: () => {profile.viewport['height']} }});
    Object.defineProperty(screen, 'width',       {{ get: () => {profile.viewport['width']} }});
    Object.defineProperty(screen, 'height',      {{ get: () => {profile.viewport['height']} }});
    Object.defineProperty(screen, 'availWidth',  {{ get: () => {profile.viewport['width']} }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {profile.viewport['height'] - 40} }});
  }} catch(e) {{}}
}})();
"""


# ─── ScraperResult / BaseScraper ─────────────────────────────────────────────

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
