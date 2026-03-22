"""Discord webhook integration — sends rich embed alerts for fleece/synchilla listings."""
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Keywords that trigger an alert (case-insensitive, checked against title + description)
ALERT_KEYWORDS = {"fleece", "synchilla"}

# Platform accent colours (Discord decimal integers)
_PLATFORM_COLOURS = {
    "ebay":   0x0064D2,  # eBay blue
    "vinted": 0x05A88A,  # Vinted teal
    "depop":  0xFF2300,  # Depop red
}
_DEFAULT_COLOUR = 0x5865F2  # Discord blurple fallback

# Platform display labels
_PLATFORM_LABELS = {
    "ebay":   "eBay",
    "vinted": "Vinted",
    "depop":  "Depop",
}

# Platform emoji icons
_PLATFORM_ICONS = {
    "ebay":   "🔵",
    "vinted": "🌿",
    "depop":  "🌹",
}


def matches_keyword(listing: dict) -> bool:
    """Return True if title or description contains any alert keyword (case-insensitive)."""
    haystack = " ".join(filter(None, [
        listing.get("title") or "",
        listing.get("description") or "",
    ])).lower()
    return any(kw in haystack for kw in ALERT_KEYWORDS)


def _build_embed(listing: dict) -> dict:
    site = (listing.get("site") or "").lower()
    colour = _PLATFORM_COLOURS.get(site, _DEFAULT_COLOUR)
    platform_label = _PLATFORM_LABELS.get(site, site.capitalize())
    platform_icon  = _PLATFORM_ICONS.get(site, "🛍️")

    title = listing.get("title") or "Untitled listing"
    url   = listing.get("listing_url") or None
    desc  = listing.get("description") or listing.get("condition") or ""
    if len(desc) > 200:
        desc = desc[:197] + "…"

    price_raw = listing.get("price")
    price_str = f"£{float(price_raw):.2f}" if price_raw is not None else "—"

    # Build inline fields — only show fields that have a value
    fields = []

    fields.append({"name": "💷  Price", "value": f"**{price_str}**", "inline": True})

    if listing.get("size"):
        fields.append({"name": "📏  Size", "value": listing["size"], "inline": True})

    if listing.get("condition"):
        fields.append({"name": "✅  Condition", "value": listing["condition"], "inline": True})

    if listing.get("brand"):
        fields.append({"name": "🏷️  Brand", "value": listing["brand"], "inline": True})

    if listing.get("seller"):
        fields.append({"name": "👤  Seller", "value": listing["seller"], "inline": True})

    fields.append({"name": "🛒  Platform", "value": platform_label, "inline": True})

    embed: dict = {
        "color": colour,
        "author": {
            "name": f"{platform_icon}  New fleece detected on {platform_label}",
        },
        "title": title[:256],
        "fields": fields,
        "footer": {
            "text": "Marketplace Scraper  •  Fleece Alert",
        },
    }

    if desc:
        embed["description"] = desc

    if url:
        embed["url"] = url

    if listing.get("image_url"):
        embed["thumbnail"] = {"url": listing["image_url"]}

    # Timestamp — Discord requires ISO 8601 with Z, omit entirely if unavailable
    scraped = listing.get("scraped_at")
    if scraped:
        ts = str(scraped)
        ts = ts.replace("+00:00", "Z")
        if not ts.endswith("Z"):
            ts += "Z"
        embed["timestamp"] = ts

    return embed


async def send_fleece_alert(listing: dict, webhook_url: str) -> bool:
    """
    POST a Discord embed for a single fleece listing.
    Returns True on success, False on failure.
    """
    embed = _build_embed(listing)
    payload = {
        "username": "Fleece Alert",
        "avatar_url": "https://assets.stickpng.com/images/6130b76619b055000483bbaa.png",
        "embeds": [embed],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code in (200, 204):
                logger.info(
                    f"[discord] Alert sent for listing '{listing.get('title', '')[:60]}' "
                    f"({listing.get('site')})"
                )
                return True
            logger.warning(
                f"[discord] Webhook returned {resp.status_code}: {resp.text[:200]}"
            )
            return False
    except Exception as exc:
        logger.error(f"[discord] Failed to send alert: {exc}")
        return False


async def get_webhook_url(db=None) -> Optional[str]:
    """Return webhook URL: DB setting → DISCORD_WEBHOOK_URL env var → None."""
    if db is not None:
        try:
            url = await db.get_setting("discord_webhook_url")
            if url and url.strip():
                return url.strip()
        except Exception:
            pass
    return os.getenv("DISCORD_WEBHOOK_URL", "").strip() or None


async def send_test_alert(webhook_url: str) -> tuple[bool, str]:
    """Send a test embed. Returns (success, message)."""
    from datetime import datetime, timezone
    test_listing = {
        "site": "vinted",
        "title": "Patagonia Synchilla Snap-T Fleece Pullover — Test Alert",
        "description": "This is a test notification from Marketplace Scraper. If you see this, your Discord webhook is working correctly.",
        "price": 42.00,
        "currency": "GBP",
        "size": "M",
        "condition": "Good",
        "brand": "Patagonia",
        "seller": "marketplace_scraper",
        "image_url": "https://assets.stickpng.com/images/6130b76619b055000483bbaa.png",
        "listing_url": None,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = await send_fleece_alert(test_listing, webhook_url)
    return ok, ("Test alert sent successfully" if ok else "Failed — check logs for details")


async def send_fleece_alerts(new_listings: list[dict], webhook_url: Optional[str]) -> int:
    """
    Filter new_listings for keyword matches and send a Discord alert for each.
    Returns number of alerts sent.
    """
    if not webhook_url:
        return 0

    matching = [l for l in new_listings if matches_keyword(l)]
    if not matching:
        return 0

    sent = 0
    for listing in matching:
        ok = await send_fleece_alert(listing, webhook_url)
        if ok:
            sent += 1
    return sent
