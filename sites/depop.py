"""Depop (depop.com) site adapter — selectors and normalisation."""
import re
from typing import Any, Optional
from urllib.parse import quote_plus

SEARCH_URL = "https://www.depop.com/search/?q={query}&sort=newlyListed"
# Depop also exposes a JSON API — much easier to scrape
API_URL = "https://webapi.depop.com/api/v2/search/products/?q={query}&sort=newlyListed&limit=48"


def build_url(query: str) -> str:
    return SEARCH_URL.format(query=quote_plus(query))


def build_api_url(query: str, offset: int = 0) -> str:
    base = API_URL.format(query=quote_plus(query))
    if offset:
        base += f"&offset={offset}"
    return base


def extract_listing_id(url: str) -> Optional[str]:
    """Extract Depop product slug / ID from URL."""
    # Pattern: /products/username-slug-12345/  or  /products/slug/
    match = re.search(r"/products/([^/?#]+)", url)
    if match:
        slug = match.group(1)
        # Return the last dash-separated numeric token if present
        parts = slug.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[1]
        return slug
    return None


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Depop API product dict or scraped dict."""
    # API response structure
    url = raw.get("url", raw.get("listing_url", ""))
    slug = raw.get("slug", "")
    if not url and slug:
        url = f"https://www.depop.com/products/{slug}/"

    preview = raw.get("preview", {}) or {}
    price_data = raw.get("price", {}) or {}
    price_amount = (
        price_data.get("amount")
        or price_data.get("priceAmount")
        or raw.get("price_amount")
        or raw.get("price")
    )

    desc = (raw.get("description") or raw.get("title") or "").strip()
    return {
        "site": "depop",
        "listing_id": raw.get("id") or raw.get("listing_id") or extract_listing_id(url) or slug,
        "title": desc,
        "description": desc or None,
        "price": _parse_price(price_amount),
        "currency": price_data.get("currencyName", "GBP"),
        "brand": raw.get("brandName") or raw.get("brand") or None,
        "size": (raw.get("size", {}) or {}).get("label") or raw.get("size") or None,
        "condition": None,  # not in Depop API
        "seller": (raw.get("seller", {}) or {}).get("username") or raw.get("seller") or None,
        "image_url": (
            (preview.get("images") or [{}])[0].get("url")
            or raw.get("image_url")
            or None
        ),
        "listing_url": url or None,
        "raw": raw,
    }


def _parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 100 if value > 1000 else float(value)
    text = str(value).replace(",", "").replace("£", "").strip()
    match = re.search(r"[\d.]+", text)
    return float(match.group()) if match else None


# Playwright CSS selectors for SPA scraping
SELECTORS = {
    "product_card": "article[data-testid='product-card']",
    "title": "p[data-testid='product-card__description']",
    "price": "p[data-testid='product-card__price']",
    "image": "img[data-testid='product-card__image']",
    "link": "a[data-testid='product-card__link']",
    "seller": "a[data-testid='product-card__seller']",
    "likes": "p[data-testid='product-card__likes']",
}
