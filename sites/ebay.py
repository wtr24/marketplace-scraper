"""eBay (ebay.co.uk) site adapter — selectors and normalisation."""
import re
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse, parse_qs

# _sop=10 = newly listed
SEARCH_URL = "https://www.ebay.co.uk/sch/i.html?_nkw={query}&_sop=10"


def build_url(query: str) -> str:
    return SEARCH_URL.format(query=quote_plus(query))


def extract_listing_id(url: str) -> Optional[str]:
    """Extract eBay item ID from URL."""
    # Pattern 1: /itm/item-title/123456789012
    match = re.search(r"/itm/[^/]*/(\d{10,})", url)
    if match:
        return match.group(1)
    # Pattern 2: /itm/123456789012
    match = re.search(r"/itm/(\d{10,})", url)
    if match:
        return match.group(1)
    # Pattern 3: query param ?item=
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "item" in qs:
        return qs["item"][0]
    return None


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    url = raw.get("url", raw.get("listing_url", ""))
    condition = raw.get("condition", "").strip()
    return {
        "site": "ebay",
        "listing_id": raw.get("listing_id") or extract_listing_id(url) or "",
        "title": raw.get("title", "").strip(),
        "description": condition or None,  # condition text is the closest to a description from search results
        "price": _parse_price(raw.get("price")),
        "currency": raw.get("currency", "GBP"),
        "brand": None,
        "size": None,
        "condition": condition or None,
        "seller": raw.get("seller", "").strip() or None,
        "image_url": raw.get("image_url", "") or None,
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


# CSS selectors for Playwright — updated for eBay 2025 card redesign
# eBay migrated from li.s-item to li.s-card with su-* component classes
SELECTORS = {
    "result_list": "ul.srp-results",
    "item": "li.s-card",
    "title": "span.su-styled-text.primary.default",   # leaf span, clean text
    "price": "span.s-card__price",                    # bold price span
    "condition": "span.su-styled-text.secondary.default",  # "Brand new" / "Used" etc.
    "shipping": None,
    "seller": None,                                   # not shown on new search cards
    "link": "a[href*='/itm/']",
    "image": "img.s-card__image",
    "end_time": None,
}
