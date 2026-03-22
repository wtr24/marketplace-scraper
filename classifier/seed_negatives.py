"""Seed DON'T WANT class from non-Synchilla DB listings."""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DONT_WANT_DIR = Path("data/classifier/dont_want")
LABELS_FILE = Path("data/classifier/labels.json")
API_BASE = "http://localhost:3000"
PAGE_SIZE = 200
CAP = 600

SYNCHILLA_KEYWORDS = re.compile(
    r"synchilla|snap[-\s]?t|patagonia\s+fleece", re.IGNORECASE
)


def is_synchilla_title(title: str) -> bool:
    return bool(SYNCHILLA_KEYWORDS.search(title))


def _load_labels() -> dict:
    if LABELS_FILE.exists():
        return json.loads(LABELS_FILE.read_text())
    return {}


def _save_labels(labels: dict):
    LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LABELS_FILE.write_text(json.dumps(labels, indent=2))


async def seed():
    DONT_WANT_DIR.mkdir(parents=True, exist_ok=True)
    labels = _load_labels()
    downloaded = 0
    offset = 0

    async with httpx.AsyncClient(timeout=30, base_url=API_BASE) as client:
        while downloaded < CAP:
            resp = await client.get(f"/api/listings?limit={PAGE_SIZE}&offset={offset}")
            resp.raise_for_status()
            data = resp.json()
            listings = data.get("listings", data) if isinstance(data, dict) else data
            if not listings:
                break

            negatives = [
                l for l in listings
                if l.get("image_url") and not is_synchilla_title(l.get("title", ""))
            ]

            tasks = [_download_entry(client, l, labels) for l in negatives]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            newly = sum(1 for r in results if r is True)
            downloaded += newly
            offset += PAGE_SIZE

            logger.info(f"Page offset={offset}: {newly} new negatives (total={downloaded})")

            if len(listings) < PAGE_SIZE:
                break

    _save_labels(labels)
    logger.info(f"Negatives seed complete. Downloaded {downloaded} images.")
    return downloaded


async def _download_entry(client: httpx.AsyncClient, listing: dict, labels: dict) -> bool:
    url = listing["image_url"]
    filename = re.sub(r"[^\w.]", "_", os.path.basename(url.split("?")[0]))[:80]
    dest = DONT_WANT_DIR / f"{listing['id']}_{filename}"

    if dest.exists():
        return False

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as dl:
            resp = await dl.get(url)
            resp.raise_for_status()
        dest.write_bytes(resp.content)
        labels[str(dest)] = {
            "label": "dont_want",
            "source": "db_negative",
            "listing_id": listing["id"],
            "title": listing.get("title", ""),
        }
        return True
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed())
