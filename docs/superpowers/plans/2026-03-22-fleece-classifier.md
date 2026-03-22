# Fleece Classifier Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PyTorch image classifier that filters Discord alerts to only vintage/limited-edition Patagonia Synchilla and Snap-T fleeces.

**Architecture:** EfficientNet-B0 trained locally and exported as ONNX. Seed scripts auto-label known positives (archive) and negatives (DB). A Tinder-style labeller UI handles the ambiguous 568 Synchilla DB listings. Inference runs async in the scheduler via onnxruntime, falling back to keyword-only if the model file is absent.

**Tech Stack:** Python, FastAPI (existing), httpx (existing), onnxruntime, torch + timm + torchvision (training only), vanilla JS (labeller UI)

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `classifier/__init__.py` | Create | FleeceClassifier: load ONNX, async classify(), _infer() |
| `classifier/seed_archive.py` | Create | Download WANT images from oldschooloutdoor.com Shopify API |
| `classifier/seed_negatives.py` | Create | Download DON'T WANT images from local /api/listings |
| `classifier/train.py` | Create | EfficientNet-B0 training + ONNX export (run locally) |
| `classifier/fleece_classifier.onnx` | Create (post-training) | Trained model — tracked in git for Watchtower deployment |
| `ui/labeller.html` | Create | Tinder swipe UI — J/L/S keys, progress bar |
| `main.py` | Modify | Add FleeceClassifier to lifespan, add /labeller route + 3 API endpoints |
| `scheduler.py` | Modify | Call classifier after upsert_listings, before send_fleece_alerts |
| `requirements.txt` | Modify | Add onnxruntime==1.18.1 |
| `tests/test_classifier.py` | Create | Unit tests for FleeceClassifier |
| `tests/test_labeller_api.py` | Create | Tests for /api/labeller/* endpoints |
| `.gitignore` | Modify | Add data/classifier/ |

---

## Task 1: Scaffold + gitignore

**Files:**
- Create: `classifier/__init__.py` (empty stub)
- Modify: `.gitignore`
- Modify: `requirements.txt`

- [ ] **Step 1: Create classifier package**

```bash
mkdir -p classifier
touch classifier/__init__.py
mkdir -p data/classifier/want data/classifier/dont_want data/classifier/cache
```

- [ ] **Step 2: Update .gitignore**

Add to `.gitignore`:
```
data/classifier/
```
Do NOT add `classifier/fleece_classifier.onnx` — it must be tracked.

- [ ] **Step 3: Add onnxruntime to requirements.txt**

Add line:
```
onnxruntime==1.18.1
```

- [ ] **Step 4: Commit**

```bash
git add classifier/ .gitignore requirements.txt
git commit -m "feat: scaffold classifier package"
```

---

## Task 2: `classifier/seed_archive.py`

Seeds the WANT class with ~70 known-good Patagonia Synchilla images from oldschooloutdoor.com's public Shopify JSON API. Fully resumable.

**Files:**
- Create: `classifier/seed_archive.py`
- Create: `tests/test_seed_archive.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seed_archive.py
import json, os
from unittest.mock import patch, MagicMock
from classifier.seed_archive import extract_images_from_shopify_response

def test_extract_images_from_shopify_response():
    fake_response = {
        "products": [
            {
                "title": "1992 Diamonds Synchilla Snap-T",
                "images": [{"src": "https://cdn.shopify.com/test/image1.jpg"}]
            },
            {
                "title": "No Image Product",
                "images": []
            }
        ]
    }
    results = extract_images_from_shopify_response(fake_response)
    assert len(results) == 1
    assert results[0]["title"] == "1992 Diamonds Synchilla Snap-T"
    assert results[0]["image_url"] == "https://cdn.shopify.com/test/image1.jpg"
    assert results[0]["label"] == "want"
    assert results[0]["source"] == "archive"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_seed_archive.py -v
```
Expected: `ImportError: cannot import name 'extract_images_from_shopify_response'`

- [ ] **Step 3: Implement `classifier/seed_archive.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_seed_archive.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add classifier/seed_archive.py tests/test_seed_archive.py
git commit -m "feat: add archive seed script for WANT class"
```

---

## Task 3: `classifier/seed_negatives.py`

Seeds DON'T WANT class by paginating the local API and downloading non-Synchilla listing images.

**Files:**
- Create: `classifier/seed_negatives.py`
- Create: `tests/test_seed_negatives.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seed_negatives.py
from classifier.seed_negatives import is_synchilla_title

def test_is_synchilla_title_matches():
    assert is_synchilla_title("Patagonia Synchilla Snap-T Blue") is True
    assert is_synchilla_title("patagonia snap t fleece") is True
    assert is_synchilla_title("Patagonia Fleece Jacket") is True

def test_is_synchilla_title_no_match():
    assert is_synchilla_title("North Face fleece jacket") is False
    assert is_synchilla_title("Adidas tracksuit top") is False
    assert is_synchilla_title("Patagonia Nano Puff") is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_seed_negatives.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement `classifier/seed_negatives.py`**

```python
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
            listings = resp.json()
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_seed_negatives.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add classifier/seed_negatives.py tests/test_seed_negatives.py
git commit -m "feat: add negatives seed script for DON'T WANT class"
```

---

## Task 4: Labeller API endpoints

Three new routes in `main.py`: `/api/labeller/next`, `/api/labeller/label`, `/api/labeller/progress`.

**Files:**
- Modify: `main.py`
- Create: `tests/test_labeller_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_labeller_api.py
import json
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.anyio
async def test_labeller_progress_returns_counts():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/labeller/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert "labelled" in data
    assert "total" in data
    assert "want_count" in data
    assert "dont_want_count" in data

@pytest.mark.anyio
async def test_labeller_next_returns_listing_or_done():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/labeller/next")
    assert resp.status_code == 200
    data = resp.json()
    # Either a listing with image_url, or {"done": true}
    assert "done" in data or "image_url" in data

@pytest.mark.anyio
async def test_labeller_label_invalid_value():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/labeller/label", json={"listing_id": 1, "label": "invalid"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_labeller_api.py -v
```
Expected: FAIL (404 on all labeller routes)

- [ ] **Step 3: Add labeller routes to `main.py`**

Add after existing imports:
```python
import random
from pathlib import Path
```

Add Pydantic model:
```python
class LabelRequest(BaseModel):
    listing_id: int
    label: str

    @field_validator("label")
    @classmethod
    def validate_label(cls, v):
        if v not in ("want", "dont_want", "skip"):
            raise ValueError("label must be want, dont_want, or skip")
        return v
```

Add helper at module level:
```python
LABELS_FILE = Path("data/classifier/labels.json")
SYNCHILLA_KEYWORDS = {"synchilla", "snap-t", "snap t", "patagonia fleece"}

def _load_labels() -> dict:
    if LABELS_FILE.exists():
        return json.loads(LABELS_FILE.read_text())
    return {}

def _save_labels(labels: dict):
    LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LABELS_FILE.write_text(json.dumps(labels, indent=2))

def _is_synchilla(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in SYNCHILLA_KEYWORDS)
```

Add routes:
```python
@app.get("/api/labeller/progress")
async def labeller_progress():
    labels = _load_labels()
    want = sum(1 for v in labels.values() if v.get("label") == "want" and v.get("source") == "user")
    dont_want = sum(1 for v in labels.values() if v.get("label") == "dont_want" and v.get("source") == "user")
    synchilla_ids = {
        r["id"] for r in await db.get_listings(limit=10000, offset=0)
        if _is_synchilla(r.get("title", ""))
    }
    return {"labelled": want + dont_want, "total": len(synchilla_ids),
            "want_count": want, "dont_want_count": dont_want}


@app.get("/api/labeller/next")
async def labeller_next():
    labels = _load_labels()
    labelled_ids = {v.get("listing_id") for v in labels.values() if v.get("source") == "user"}
    listings = await db.get_listings(limit=10000, offset=0)
    candidates = [
        r for r in listings
        if _is_synchilla(r.get("title", ""))
        and r.get("image_url")
        and r["id"] not in labelled_ids
    ]
    if not candidates:
        return {"done": True, "remaining": 0}
    item = random.choice(candidates)
    return {
        "id": item["id"], "image_url": item["image_url"],
        "title": item.get("title"), "price": item.get("price"),
        "site": item.get("site"), "size": item.get("size"),
        "condition": item.get("condition"),
        "remaining": len(candidates),
    }


@app.post("/api/labeller/label")
async def labeller_label(req: LabelRequest):
    if req.label == "skip":
        return {"status": "skipped"}
    labels = _load_labels()
    labels[f"user_{req.listing_id}"] = {
        "label": req.label, "source": "user", "listing_id": req.listing_id
    }
    _save_labels(labels)
    return {"status": "saved", "label": req.label}


@app.get("/labeller")
async def labeller_ui():
    return FileResponse("ui/labeller.html")
```

- [ ] **Step 4: Check `db.get_listings` signature in `db/database.py` — confirm it accepts `limit` and `offset` params. If method name differs, adjust accordingly.**

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_labeller_api.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_labeller_api.py
git commit -m "feat: add labeller API endpoints and /labeller route"
```

---

## Task 5: Labeller UI (`ui/labeller.html`)

Single HTML file, no build step. Keyboard-driven swipe interface.

**Files:**
- Create: `ui/labeller.html`

- [ ] **Step 1: Create `ui/labeller.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Fleece Labeller</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f0f0f; color: #fff; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
  #app { width: 420px; max-width: 100vw; padding: 16px; }
  #header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  #progress-bar { background: #333; border-radius: 4px; height: 6px; margin-bottom: 16px; }
  #progress-fill { background: #22c55e; height: 100%; border-radius: 4px; transition: width 0.3s; }
  #image-wrap { position: relative; width: 100%; aspect-ratio: 1; background: #1a1a1a; border-radius: 12px; overflow: hidden; margin-bottom: 16px; }
  #listing-img { width: 100%; height: 100%; object-fit: cover; }
  #meta { text-align: center; margin-bottom: 20px; }
  #title { font-size: 1rem; font-weight: 600; margin-bottom: 4px; }
  #sub { font-size: 0.85rem; color: #888; }
  #buttons { display: flex; gap: 12px; }
  .btn { flex: 1; padding: 14px; border: none; border-radius: 10px; font-size: 1rem; font-weight: 700; cursor: pointer; transition: transform 0.1s, opacity 0.1s; }
  .btn:active { transform: scale(0.96); }
  #btn-no { background: #ef4444; color: #fff; }
  #btn-yes { background: #22c55e; color: #fff; }
  #btn-skip { background: #374151; color: #9ca3af; flex: 0; padding: 14px 20px; font-size: 0.85rem; }
  #done { text-align: center; padding: 40px; font-size: 1.2rem; color: #22c55e; display: none; }
  .hint { font-size: 0.75rem; color: #555; text-align: center; margin-top: 12px; }
</style>
</head>
<body>
<div id="app">
  <div id="header">
    <span id="count-label" style="font-size:0.85rem;color:#888"></span>
    <span id="eta" style="font-size:0.85rem;color:#555"></span>
  </div>
  <div id="progress-bar"><div id="progress-fill" style="width:0%"></div></div>
  <div id="image-wrap"><img id="listing-img" src="" alt=""></div>
  <div id="meta">
    <div id="title"></div>
    <div id="sub"></div>
  </div>
  <div id="buttons">
    <button class="btn" id="btn-no" onclick="label('dont_want')">✗ DON'T WANT</button>
    <button class="btn" id="btn-skip" onclick="label('skip')">Skip</button>
    <button class="btn" id="btn-yes" onclick="label('want')">✓ WANT</button>
  </div>
  <div class="hint">← J = Don't Want &nbsp;|&nbsp; L → = Want &nbsp;|&nbsp; S = Skip</div>
  <div id="done">✓ All labelled! Run train.py next.</div>
</div>
<script>
let current = null;
let startTime = Date.now();
let labelled = 0;

async function loadNext() {
  const [nextResp, progResp] = await Promise.all([
    fetch('/api/labeller/next'),
    fetch('/api/labeller/progress')
  ]);
  const next = await nextResp.json();
  const prog = await progResp.json();

  labelled = prog.labelled;
  const total = prog.total;
  const pct = total > 0 ? Math.round((labelled / total) * 100) : 0;
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('count-label').textContent = `${labelled} / ${total} labelled`;

  const elapsed = (Date.now() - startTime) / 1000;
  const rate = labelled > 0 ? elapsed / labelled : null;
  const remaining = total - labelled;
  if (rate && remaining > 0) {
    const eta = Math.round((rate * remaining) / 60);
    document.getElementById('eta').textContent = `~${eta}m remaining`;
  }

  if (next.done) {
    document.getElementById('image-wrap').style.display = 'none';
    document.getElementById('meta').style.display = 'none';
    document.getElementById('buttons').style.display = 'none';
    document.getElementById('done').style.display = 'block';
    return;
  }

  current = next;
  document.getElementById('listing-img').src = next.image_url;
  document.getElementById('title').textContent = next.title || 'Untitled';
  document.getElementById('sub').textContent =
    [next.site, next.size, next.condition, next.price ? `£${next.price}` : null]
    .filter(Boolean).join(' · ');
}

async function label(value) {
  if (!current) return;
  await fetch('/api/labeller/label', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({listing_id: current.id, label: value})
  });
  loadNext();
}

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft' || e.key === 'j' || e.key === 'J') label('dont_want');
  if (e.key === 'ArrowRight' || e.key === 'l' || e.key === 'L') label('want');
  if (e.key === 's' || e.key === 'S') label('skip');
});

loadNext();
</script>
</body>
</html>
```

- [ ] **Step 2: Manual test**

Start server: `python main.py`
Open: `http://localhost:3000/labeller`
Verify: image loads, J/L/S keys work, progress bar updates, labels.json is written after each swipe.

- [ ] **Step 3: Commit**

```bash
git add ui/labeller.html
git commit -m "feat: add Tinder-style fleece labeller UI at /labeller"
```

---

## Task 6: `classifier/__init__.py` — FleeceClassifier

Async ONNX inference module. Safe to use without model file (ready=False fallback).

**Files:**
- Modify: `classifier/__init__.py`
- Create: `tests/test_classifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_classifier.py
import pytest
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock
from classifier import FleeceClassifier

def test_classifier_ready_false_when_no_model():
    clf = FleeceClassifier("nonexistent_model.onnx")
    assert clf.ready is False

def test_classifier_classify_returns_want_true_when_not_ready():
    clf = FleeceClassifier("nonexistent_model.onnx")
    import asyncio
    result = asyncio.run(clf.classify("http://example.com/img.jpg"))
    assert result["want"] is True
    assert "confidence" in result

def test_classifier_infer_returns_probability():
    clf = FleeceClassifier("nonexistent_model.onnx")
    clf.ready = True
    mock_session = MagicMock()
    mock_session.run.return_value = [np.array([[1.0, 3.0]])]  # logits: dont_want=1, want=3
    clf._session = mock_session
    prob = clf._infer(np.zeros((1, 3, 224, 224), dtype=np.float32))
    assert 0.5 < prob < 1.0  # want logit higher → prob > 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_classifier.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement `classifier/__init__.py`**

```python
"""FleeceClassifier — async ONNX inference for Synchilla/Snap-T detection."""
import asyncio
import io
import logging
import os
import time
from pathlib import Path

import httpx
import numpy as np

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/classifier/cache")
CACHE_TTL = 86400  # 24 hours


class FleeceClassifier:
    def __init__(self, model_path: str):
        self.ready = False
        self._session = None
        self._load(model_path)

    def _load(self, model_path: str):
        if not os.path.exists(model_path):
            logger.warning(f"[classifier] Model not found at {model_path} — running keyword-only mode")
            return
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            self.ready = True
            logger.info(f"[classifier] Loaded model from {model_path}")
        except Exception as e:
            logger.error(f"[classifier] Failed to load model: {e}")

    async def classify(self, image_url: str, threshold: float = 0.35) -> dict:
        """Classify listing image. Returns {want: bool, confidence: float}."""
        if not self.ready:
            return {"want": True, "confidence": 0.0}
        if not image_url:
            return {"want": True, "confidence": 0.0}
        try:
            arr = await self._fetch_and_preprocess(image_url)
            loop = asyncio.get_event_loop()
            prob = await loop.run_in_executor(None, self._infer, arr)
            return {"want": prob >= threshold, "confidence": round(prob, 4)}
        except Exception as e:
            logger.warning(f"[classifier] classify error for {image_url}: {e}")
            return {"want": True, "confidence": 0.0}

    def _infer(self, arr: np.ndarray) -> float:
        """Synchronous ONNX inference. Returns want probability."""
        logits = self._session.run(["logits"], {"image": arr})[0]
        exp = np.exp(logits - logits.max())
        probs = exp / exp.sum()
        return float(probs[0][1])  # index 1 = WANT class

    async def _fetch_and_preprocess(self, image_url: str) -> np.ndarray:
        """Download image (with cache) and return normalised NCHW float32 array."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_key = _url_to_cache_key(image_url)
        cache_path = CACHE_DIR / cache_key

        if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < CACHE_TTL:
            img_bytes = cache_path.read_bytes()
        else:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                img_bytes = resp.content
            cache_path.write_bytes(img_bytes)

        return _preprocess(img_bytes)


def _url_to_cache_key(url: str) -> str:
    import hashlib
    return hashlib.md5(url.encode()).hexdigest() + ".npy"


def _preprocess(img_bytes: bytes) -> np.ndarray:
    """Resize to 224x224, normalise with ImageNet stats, return NCHW float32."""
    from PIL import Image
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((224, 224))
    arr = np.array(img, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
```

- [ ] **Step 4: Add Pillow to requirements.txt if not present**

```bash
grep -i pillow requirements.txt || echo "Pillow>=10.0.0" >> requirements.txt
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_classifier.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add classifier/__init__.py requirements.txt tests/test_classifier.py
git commit -m "feat: add FleeceClassifier with async ONNX inference"
```

---

## Task 7: Scheduler integration

Wire FleeceClassifier into the scrape cycle. Classifier is loaded once in `main.py` lifespan and passed to scheduler.

**Files:**
- Modify: `main.py`
- Modify: `scheduler.py`
- Modify: `tests/test_api.py` (ensure existing tests still pass)

- [ ] **Step 1: Read `scheduler.py` to find `_execute_job` or equivalent function where `send_fleece_alerts` is called**

```bash
grep -n "send_fleece_alerts\|upsert_listings" scheduler.py
```

Note the exact function and line numbers.

- [ ] **Step 2: Write failing test for classifier integration**

```python
# Add to tests/test_classifier.py
@pytest.mark.anyio
async def test_scheduler_skips_alert_when_classifier_rejects():
    """Classifier returning want=False should prevent Discord alert."""
    from unittest.mock import AsyncMock, patch
    mock_clf = MagicMock()
    mock_clf.ready = True
    mock_clf.classify = AsyncMock(return_value={"want": False, "confidence": 0.9})

    listings = [{"id": 1, "title": "Patagonia Synchilla fleece", "image_url": "http://x.com/img.jpg",
                 "site": "vinted", "price": 30}]

    with patch("scheduler.classifier", mock_clf), \
         patch("scheduler.send_fleece_alerts", new_callable=AsyncMock) as mock_alert:
        from scheduler import _filter_by_classifier
        filtered = await _filter_by_classifier(listings)
        assert filtered == []
```

- [ ] **Step 3: Add `_filter_by_classifier` to `scheduler.py`**

Add at the top of `scheduler.py`:
```python
import asyncio
from classifier import FleeceClassifier
classifier: FleeceClassifier = FleeceClassifier.__new__(FleeceClassifier)
classifier.ready = False  # placeholder until main.py injects real instance
```

Add function:
```python
async def _filter_by_classifier(listings: list[dict]) -> list[dict]:
    """Filter listings through FleeceClassifier. Falls back to full list if not ready."""
    if not classifier.ready:
        return listings
    results = await asyncio.gather(*[
        classifier.classify(item.get("image_url")) for item in listings
    ])
    return [item for item, r in zip(listings, results) if r["want"]]
```

In `main.py` lifespan, after `db.init()`:
```python
from classifier import FleeceClassifier
import scheduler as sched
sched.classifier = FleeceClassifier("classifier/fleece_classifier.onnx")
```

In `scheduler.py`, after `upsert_listings` returns `new_items`, add:
```python
new_items = await _filter_by_classifier(new_items)
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/ -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scheduler.py main.py tests/test_classifier.py
git commit -m "feat: wire FleeceClassifier into scheduler alert pipeline"
```

---

## Task 8: `classifier/train.py`

Local training script. Not run on NAS. No unit tests (training scripts are validated by running them).

**Files:**
- Create: `classifier/train.py`
- Create: `classifier/requirements-training.txt`

- [ ] **Step 1: Create `classifier/requirements-training.txt`**

```
# Install on local machine only — NOT on NAS
# pip install -r classifier/requirements-training.txt
--index-url https://download.pytorch.org/whl/cpu
torch==2.3.1
torchvision==0.18.1
timm==1.0.9
```

- [ ] **Step 2: Create `classifier/train.py`**

```python
"""
EfficientNet-B0 training script for Patagonia Synchilla / Snap-T classifier.

Usage (local machine only):
    pip install -r classifier/requirements-training.txt
    python -m classifier.train

Outputs:
    classifier/fleece_classifier.onnx
"""
import json
import logging
import os
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import transforms
import timm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WANT_DIR = Path("data/classifier/want")
DONT_WANT_DIR = Path("data/classifier/dont_want")
LABELS_FILE = Path("data/classifier/labels.json")
OUTPUT_ONNX = Path("classifier/fleece_classifier.onnx")
EPOCHS_PHASE1 = 15
EPOCHS_PHASE2 = 20
EARLY_STOP_PATIENCE = 5
BATCH_SIZE = 32
THRESHOLD = 0.35


# ─── Dataset ─────────────────────────────────────────────────────────────────

class FleeceDataset(Dataset):
    def __init__(self, samples: list[tuple[Path, int]], transform):
        self.samples = samples
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        return self.transform(img), label


def load_samples() -> tuple[list, list]:
    """Returns (train_samples, val_samples) as lists of (Path, label) tuples."""
    want_files = list(WANT_DIR.glob("*.jpg")) + list(WANT_DIR.glob("*.png")) + list(WANT_DIR.glob("*.webp"))
    dont_files = list(DONT_WANT_DIR.glob("*.jpg")) + list(DONT_WANT_DIR.glob("*.png")) + list(DONT_WANT_DIR.glob("*.webp"))

    # Also include user-labelled images from labels.json
    if LABELS_FILE.exists():
        labels = json.loads(LABELS_FILE.read_text())
        for path_str, meta in labels.items():
            p = Path(path_str)
            if not p.exists():
                continue
            if meta["label"] == "want" and meta["source"] == "user":
                want_files.append(p)
            elif meta["label"] == "dont_want" and meta["source"] == "user":
                dont_files.append(p)

    logger.info(f"Dataset: {len(want_files)} WANT, {len(dont_files)} DONT_WANT")
    assert len(want_files) >= 50, f"Too few WANT samples: {len(want_files)}. Run seed_archive.py and label more."
    assert len(dont_files) >= 50, f"Too few DONT_WANT samples: {len(dont_files)}. Run seed_negatives.py."

    # Stratified 80/20 split
    def split(files):
        n = int(len(files) * 0.8)
        idxs = torch.randperm(len(files)).tolist()
        return [files[i] for i in idxs[:n]], [files[i] for i in idxs[n:]]

    want_tr, want_val = split(want_files)
    dont_tr, dont_val = split(dont_files)

    train = [(p, 1) for p in want_tr] + [(p, 0) for p in dont_tr]
    val = [(p, 1) for p in want_val] + [(p, 0) for p in dont_val]
    return train, val


# ─── Transforms ──────────────────────────────────────────────────────────────

TRAIN_TF = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.6, 1.0)),
    transforms.RandomHorizontalFlip(),
    transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3, hue=0.05),
    transforms.RandomRotation(15),
    transforms.RandomPerspective(distortion_scale=0.2, p=0.3),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])
VAL_TF = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


# ─── Training ────────────────────────────────────────────────────────────────

def make_loader(samples, transform, shuffle=False, sampler=None):
    ds = FleeceDataset(samples, transform)
    return DataLoader(ds, batch_size=BATCH_SIZE, shuffle=shuffle, sampler=sampler, num_workers=0)


def train_phase(model, loader, val_loader, optimizer, criterion, epochs, patience, label):
    best_acc, no_improve = 0.0, 0
    for epoch in range(epochs):
        model.train()
        for imgs, labels in loader:
            optimizer.zero_grad()
            loss = criterion(model(imgs), labels)
            loss.backward()
            optimizer.step()

        acc = evaluate(model, val_loader)
        logger.info(f"[{label}] Epoch {epoch+1}/{epochs} val_acc={acc:.3f}")
        if acc > best_acc:
            best_acc = acc
            torch.save(model.state_dict(), str(Path(os.environ.get("TEMP", "/tmp")) / "fleece_best.pt"))
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                logger.info(f"[{label}] Early stop at epoch {epoch+1}")
                break

    model.load_state_dict(torch.load(str(Path(os.environ.get("TEMP", "/tmp")) / "fleece_best.pt")))
    return best_acc


def evaluate(model, loader) -> float:
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for imgs, labels in loader:
            preds = model(imgs).argmax(1)
            correct += (preds == labels).sum().item()
            total += len(labels)
    return correct / total if total > 0 else 0.0


def main():
    train_samples, val_samples = load_samples()

    # Class weights for imbalance
    want_n = sum(1 for _, l in train_samples if l == 1)
    dont_n = sum(1 for _, l in train_samples if l == 0)
    class_weights = torch.tensor([1.0, dont_n / want_n])
    sample_weights = torch.tensor([class_weights[l].item() for _, l in train_samples])
    sampler = WeightedRandomSampler(sample_weights, len(sample_weights))

    train_loader = make_loader(train_samples, TRAIN_TF, sampler=sampler)
    val_loader = make_loader(val_samples, VAL_TF)

    # Model
    model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=0)
    head = nn.Sequential(
        nn.Dropout(0.3), nn.Linear(1280, 256), nn.ReLU(),
        nn.Dropout(0.2), nn.Linear(256, 2)
    )
    model = nn.Sequential(model, head)

    criterion = nn.CrossEntropyLoss(weight=class_weights)

    # Phase 1: frozen backbone
    for p in list(model.parameters())[:-len(list(head.parameters()))]:
        p.requires_grad_(False)
    opt1 = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=1e-3, weight_decay=1e-4)
    logger.info("Phase 1: training head only")
    train_phase(model, train_loader, val_loader, opt1, criterion, EPOCHS_PHASE1, EARLY_STOP_PATIENCE, "P1")

    # Phase 2: unfreeze top 30%
    all_params = list(model.parameters())
    for p in all_params[int(len(all_params) * 0.7):]:
        p.requires_grad_(True)
    opt2 = torch.optim.Adam([
        {"params": all_params[:int(len(all_params)*0.7)], "lr": 1e-5},
        {"params": all_params[int(len(all_params)*0.7):], "lr": 1e-4},
    ], weight_decay=1e-4)
    logger.info("Phase 2: selective unfreeze")
    final_acc = train_phase(model, train_loader, val_loader, opt2, criterion, EPOCHS_PHASE2, EARLY_STOP_PATIENCE, "P2")

    logger.info(f"Final val accuracy: {final_acc:.3f}")
    if final_acc < 0.80:
        logger.warning("Accuracy below 0.80 — label more data before deploying")

    # Export ONNX
    model.eval()
    dummy = torch.randn(1, 3, 224, 224)
    torch.onnx.export(
        model, dummy, str(OUTPUT_ONNX),
        input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}},
        opset_version=17,
    )
    logger.info(f"Exported model to {OUTPUT_ONNX}")
    logger.info("Next: git add classifier/fleece_classifier.onnx && git commit && git push")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add classifier/train.py classifier/requirements-training.txt
git commit -m "feat: add EfficientNet-B0 training script with ONNX export"
```

---

## Task 9: Final integration check

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all tests pass, no regressions

- [ ] **Step 2: Smoke test the labeller locally**

```bash
python main.py &
curl http://localhost:3000/api/labeller/progress
curl http://localhost:3000/api/labeller/next
```
Expected: JSON with counts and a listing with image_url

- [ ] **Step 3: Run seed scripts against live server**

```bash
python -m classifier.seed_archive
python -m classifier.seed_negatives
```
Expected: images appear in `data/classifier/want/` and `data/classifier/dont_want/`

- [ ] **Step 4: Verify fallback — no ONNX file**

```bash
python -c "from classifier import FleeceClassifier; c = FleeceClassifier('nope.onnx'); print(c.ready)"
```
Expected: `False` (no crash, just logs a warning)

- [ ] **Step 5: Final commit and push**

```bash
git add -A
git commit -m "feat: complete fleece classifier — seed scripts, labeller UI, inference integration"
git push origin main
```

---

## Post-implementation: Training workflow

Once you've labelled enough images via `/labeller`:

```bash
# On your local machine:
pip install -r classifier/requirements-training.txt
python -m classifier.train

# If val accuracy >= 80%:
git add classifier/fleece_classifier.onnx
git commit -m "model: train fleece classifier v1 (val_acc=XX%)"
git push origin main
# Watchtower deploys to NAS automatically within ~60s
```
