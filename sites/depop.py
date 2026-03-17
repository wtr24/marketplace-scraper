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


_CONDITION_MAP = {
    "UsedCondition": "Used",
    "NewCondition": "New",
    "RefurbishedCondition": "Refurbished",
    "DamagedCondition": "Damaged",
}


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Depop scraped dict (two-phase Playwright) or legacy API dict."""
    url = raw.get("url", raw.get("listing_url", ""))
    slug = raw.get("slug", "")
    if not url and slug:
        url = f"https://www.depop.com/products/{slug}/"

    # Price: handle both API format (dict) and flat format (string/number)
    price_val = raw.get("price")
    if isinstance(price_val, dict):
        price_data = price_val or {}
        price_amount = price_data.get("amount") or price_data.get("priceAmount") or raw.get("price_amount")
        currency = price_data.get("currencyName", raw.get("currency", "GBP"))
    else:
        price_amount = price_val or raw.get("price_amount")
        currency = raw.get("currency", "GBP")

    # Title: explicit title field takes priority over description
    title = (raw.get("title") or raw.get("description") or "").strip()
    desc = (raw.get("description") or raw.get("title") or "").strip()

    # Condition: clean up schema.org URLs (e.g. "https://schema.org/UsedCondition" → "Used")
    condition_raw = raw.get("condition") or ""
    if condition_raw and "schema.org/" in condition_raw:
        key = condition_raw.rstrip("/").split("/")[-1]
        condition_raw = _CONDITION_MAP.get(key, key.replace("Condition", ""))
    condition_raw = condition_raw.strip() or None

    # Brand: handle dict (API) or string (new scraper)
    brand_val = raw.get("brand") or raw.get("brandName")
    brand = (brand_val.get("name") if isinstance(brand_val, dict) else brand_val) or None

    # Size: handle dict (API: {"label": "M"}) or string
    size_val = raw.get("size")
    size = (size_val.get("label") if isinstance(size_val, dict) else size_val) or None

    # Seller: handle dict (API: {"username": "..."}) or string
    seller_val = raw.get("seller")
    seller = (seller_val.get("username") if isinstance(seller_val, dict) else seller_val) or None

    # Image: handle API preview format or flat image_url
    preview = raw.get("preview", {}) or {}
    image_url = (
        (preview.get("images") or [{}])[0].get("url")
        or raw.get("image_url")
        or None
    )

    return {
        "site": "depop",
        "listing_id": raw.get("id") or raw.get("listing_id") or extract_listing_id(url) or slug,
        "title": title or None,
        "description": desc or None,
        "price": _parse_price(price_amount),
        "currency": currency,
        "brand": brand,
        "size": size,
        "condition": condition_raw,
        "seller": seller,
        "image_url": image_url,
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
