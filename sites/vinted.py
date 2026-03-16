"""Vinted (vinted.co.uk) site adapter — selectors and normalisation."""
import re
from typing import Any, Optional
from urllib.parse import quote_plus, urlencode, urlparse, parse_qs

SEARCH_URL = "https://www.vinted.co.uk/catalog?search_text={query}&order=newest_first"


def build_url(query: str) -> str:
    return SEARCH_URL.format(query=quote_plus(query))


def extract_listing_id(url: str) -> Optional[str]:
    """Extract the numeric item ID from a Vinted listing URL."""
    # URL pattern: /items/1234567890-item-title
    match = re.search(r"/items/(\d+)", url)
    if match:
        return match.group(1)
    # fallback: last path segment numeric prefix
    path = urlparse(url).path
    parts = path.rstrip("/").split("/")
    last = parts[-1] if parts else ""
    m = re.match(r"(\d+)", last)
    return m.group(1) if m else last


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw Vinted item dict to the standard schema."""
    url = raw.get("url", raw.get("listing_url", ""))
    return {
        "site": "vinted",
        "listing_id": raw.get("listing_id") or extract_listing_id(url) or "",
        "title": raw.get("title", "").strip(),
        "price": _parse_price(raw.get("price")),
        "currency": raw.get("currency", "GBP"),
        "brand": raw.get("brand", "").strip() or None,
        "size": raw.get("size", "").strip() or None,
        "condition": raw.get("condition", "").strip() or None,
        "seller": raw.get("seller", raw.get("username", "")).strip() or None,
        "image_url": raw.get("image_url", raw.get("photo", "")) or None,
        "listing_url": url or None,
        "raw": raw,
    }


def _parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("£", "").strip()
    match = re.search(r"[\d.]+", text)
    return float(match.group()) if match else None


# Playwright / Puppeteer CSS selectors
SELECTORS = {
    "grid_item": '[data-testid="grid-item"]',
    "title": '[data-testid="item-title"]',
    "price": '[data-testid="item-price"]',
    "brand": '[data-testid="item-brand"]',
    "size": '[data-testid="item-size"]',
    "condition": '[data-testid="item-status"]',
    "image": 'img[data-testid="item-photo"]',
    "seller": '[data-testid="owner-username"]',
    "link": 'a[data-testid="item-link"]',
}

# Beautiful Soup CSS selectors (fallback / static parse)
BS4_SELECTORS = {
    "grid_item": "div[data-testid='grid-item']",
    "title": "a[data-testid='item-title']",
    "price": "span[data-testid='item-price']",
}
