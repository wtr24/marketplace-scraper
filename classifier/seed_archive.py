"""Seed WANT class from oldschooloutdoor.com Shopify API."""
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

COLLECTIONS = [
    "https://oldschooloutdoor.com/collections/printed-snap-t/products.json?limit=250",
    "https://oldschooloutdoor.com/collections/solid-snap-ts/products.json?limit=250",
]
WANT_DIR = Path("data/classifier/want")
LABELS_FILE = Path("data/classifier/labels.json")


def extract_images_from_shopify_response(data: dict) -> list[dict]:
    """Extract image entries from a Shopify products JSON response."""
    results = []
    for product in data.get("products", []):
        images = product.get("images", [])
        if not images:
            continue
        results.append({
            "title": product.get("title", ""),
            "image_url": images[0]["src"],
            "label": "want",
            "source": "archive",
        })
    return results


def _load_labels() -> dict:
    if LABELS_FILE.exists():
        return json.loads(LABELS_FILE.read_text())
    return {}


def _save_labels(labels: dict):
    LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LABELS_FILE.write_text(json.dumps(labels, indent=2))


async def seed():
    WANT_DIR.mkdir(parents=True, exist_ok=True)
    labels = _load_labels()
    downloaded = 0

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in COLLECTIONS:
            logger.info(f"Fetching {url}")
            resp = await client.get(url)
            resp.raise_for_status()
            entries = extract_images_from_shopify_response(resp.json())

            tasks = [_download_entry(client, e, labels) for e in entries]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            downloaded += sum(1 for r in results if r is True)

    _save_labels(labels)
    logger.info(f"Archive seed complete. Downloaded {downloaded} new images.")
    return downloaded


async def _download_entry(client: httpx.AsyncClient, entry: dict, labels: dict) -> bool:
    """Download one image. Returns True if newly downloaded, False if skipped."""
    filename = os.path.basename(entry["image_url"].split("?")[0])
    dest = WANT_DIR / filename

    if dest.exists():
        return False  # Already downloaded

    try:
        resp = await client.get(entry["image_url"])
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        labels[str(dest)] = {"label": "want", "source": "archive", "title": entry["title"]}
        logger.debug(f"Downloaded {filename}")
        return True
    except Exception as e:
        logger.warning(f"Failed to download {entry['image_url']}: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
