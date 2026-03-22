# Fleece Classifier — Design Spec
**Date:** 2026-03-22
**Project:** marketplace-scraper (Vinted · eBay · Depop)
**Status:** Approved

---

## Goal

Train a PyTorch image classifier to identify vintage/limited-edition Patagonia Synchilla and Snap-T fleeces from listing images, replacing the current keyword-only Discord alert filter with a vision-based one.

**Want:** Vintage Synchilla/Snap-T (90s–00s era, bold colourblocked, all-over prints, Made in USA) + modern limited edition patterns
**Don't want:** Plain solid modern reissues, generic fleeces, non-Patagonia items

---

## Research Findings

### Archive Catalog (oldschooloutdoor.com)
- 47 printed Synchilla styles (1988–2024) with names, years, colorways
- 23 solid Synchilla styles (1985–2023)
- Shopify JSON API publicly accessible: `oldschooloutdoor.com/collections/printed-snap-t/products.json?limit=250`
- All images on `cdn.shopify.com` — publicly accessible, no auth required
- USA-made = pre-2001 (strong vintage signal)
- "Made in Jamaica" = early 1990s
- Style number `25521` = original 1985–1988 Snap-T Neck (no chest pocket)

### Existing DB
- 568 Synchilla/Snap-T listings already in DB
- 100% have `image_url` populated
- Split: Vinted 287, eBay 227, Depop 54
- 4,320 non-Synchilla listings available as auto-labelled negatives

### Model Selection
- **EfficientNet-B0** via `timm` — best accuracy/speed/data-efficiency for texture classification on small datasets
- Train locally (laptop), deploy ONNX to NAS — NAS only needs `onnxruntime` (15MB), not PyTorch
- 80–150ms CPU inference per image on NAS
- Minimum dataset: 300 images (150 per class). Target: 750 (250 WANT + 500 DON'T WANT)
- Decision threshold: 0.35 (favour recall — missing a vintage Synchilla is worse than a false alert)

---

## System Overview

```
classifier/
├── seed_archive.py        ← pulls 70 known styles from oldschooloutdoor.com → WANT
├── seed_negatives.py      ← pulls non-Synchilla DB listings → DONT_WANT
├── train.py               ← EfficientNet-B0 training script (run locally)
├── fleece_classifier.onnx ← trained model (committed, auto-deployed via Watchtower)
└── __init__.py            ← ONNX inference module, loaded once at startup

data/classifier/
├── want/                  ← WANT images (archive + user-labelled)
├── dont_want/             ← DON'T WANT images (auto-seeded + user-labelled)
└── labels.json            ← Tinder UI output

FastAPI: GET /labeller      ← Tinder swipe UI served from existing server
scheduler.py               ← classifier called after upsert, before Discord alert
```

**Data flow:**
```
Seed scripts (one-time)
       ↓
Tinder UI at /labeller → user labels 568 Synchilla DB listings → labels.json
       ↓
train.py: download images → train EfficientNet-B0 → export fleece_classifier.onnx
       ↓
git commit .onnx → push → Watchtower redeploys NAS → classifier active
```

---

## Component 1: Seed Scripts

### `classifier/seed_archive.py`
- Fetches `oldschooloutdoor.com/collections/printed-snap-t/products.json?limit=250`
- Fetches `oldschooloutdoor.com/collections/solid-snap-ts/products.json?limit=250`
- Downloads all product images to `data/classifier/want/`
- Writes entries to `labels.json` with `source: "archive"`, `label: "want"`
- Resumable — skips already-downloaded files
- Expected yield: ~70+ clean WANT images with known style names

### `classifier/seed_negatives.py`
- Queries `/api/listings` endpoint (paginates all 4,888 listings)
- Filters OUT titles containing "synchilla", "snap-t", "snap t", "patagonia fleece"
- Downloads images from remaining listings to `data/classifier/dont_want/`
- Caps at 600 images (maintains ~2:1 DON'T WANT:WANT ratio after labelling)
- Writes entries to `labels.json` with `source: "db_negative"`, `label: "dont_want"`
- Resumable — skips already-downloaded files

---

## Component 2: Tinder Labeller UI

**Route:** `GET /labeller` — served by existing FastAPI, new static HTML page

**Interface:**
```
┌─────────────────────────────────────────┐
│  Fleece Labeller  [342 / 568 labelled]  │
│  ████████████░░░░░░░░░  60%             │
├─────────────────────────────────────────┤
│                                         │
│           [listing image]               │
│                                         │
│  Patagonia Synchilla Snap-T — £45       │
│  Vinted · Size M · Good condition       │
│                                         │
├─────────────────────────────────────────┤
│   ← DON'T WANT          WANT →         │
│   [J key]               [L key]         │
│              [S] Skip                   │
└─────────────────────────────────────────┘
```

**Behaviour:**
- Loads 568 Synchilla/Snap-T DB listings in random order
- Already-labelled items skipped — fully resumable across sessions
- `→` or `L` → WANT, advance
- `←` or `J` → DON'T WANT, advance
- `S` → skip (deferred, shown again at end)
- Each label written immediately to `data/classifier/labels.json`
- Progress bar: labelled / total + estimated minutes remaining
- Pre-filtering: no image URL → auto-skip; archive images → never shown (already labelled)

**API endpoints added:**
- `GET /api/labeller/next` — returns next unlabelled listing (image_url, title, price, site, size, condition)
- `POST /api/labeller/label` — body: `{listing_id, label: "want"|"dont_want"|"skip"}`
- `GET /api/labeller/progress` — returns `{labelled, total, want_count, dont_want_count}`

---

## Component 3: Training Pipeline

`classifier/train.py` — run on local machine, not NAS.

```
Step 1: Load dataset
  ├── WANT: archive images + labels.json want entries
  └── DONT_WANT: db_negative images + labels.json dont_want entries

Step 2: Download missing images
  └── Async parallel fetches (httpx) → cache to data/classifier/
      Resumable — skips existing files

Step 3: Split 80/20 train/val (stratified by class)

Step 4: Phase 1 — Frozen backbone (15 epochs)
  └── EfficientNet-B0 pretrained on ImageNet (timm)
      New head: Dropout(0.3) → Linear(1280,256) → ReLU → Dropout(0.2) → Linear(256,2)
      Optimizer: Adam lr=1e-3, weight_decay=1e-4
      Loss: CrossEntropyLoss with class weights (inverse frequency)
      Sampler: WeightedRandomSampler

Step 5: Phase 2 — Selective unfreeze (20 epochs, early stopping patience=5)
  └── Unfreeze top 30% of backbone
      Differential LR: backbone=1e-5, head=1e-4

Step 6: Evaluate
  └── Prints: val accuracy, precision, recall, F1, confusion matrix

Step 7: Export
  └── fleece_classifier.onnx (opset 17, dynamic batch axis)
      Saved to classifier/fleece_classifier.onnx
```

**Augmentation (train only):**
```python
RandomResizedCrop(224, scale=(0.6, 1.0))
RandomHorizontalFlip()
ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3, hue=0.05)
RandomRotation(15)
RandomPerspective(distortion_scale=0.2, p=0.3)
Normalize(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225])
```

**New dependencies (local training only):**
```
torch==2.3.1 (CPU wheel)
torchvision==0.18.1
timm==1.0.9
```

**NAS runtime dependency only:**
```
onnxruntime==1.18.1
```

---

## Component 4: Inference Module

**`classifier/__init__.py`** — `FleeceClassifier` class

```python
class FleeceClassifier:
    def __init__(self, model_path: str):
        # Loads ONNX session if model_path exists, sets self.ready
        # Logs warning and sets ready=False if file missing

    async def classify(self, image_url: str, threshold: float = 0.35) -> dict:
        # Async — safe to call directly from the async scheduler loop
        # Downloads image via httpx AsyncClient (with 24h local cache)
        # Runs ONNX inference in executor: await loop.run_in_executor(None, self._infer, arr)
        # Returns {want: bool, confidence: float}
        # On image download failure: returns {want: True} (fail open)
        # On inference error: returns {want: True} (fail open)

    def _infer(self, arr: np.ndarray) -> float:
        # Synchronous ONNX inference — called via run_in_executor
        # Returns want_probability float
```

**`main.py` lifespan:**
```python
from classifier import FleeceClassifier
classifier = FleeceClassifier("classifier/fleece_classifier.onnx")
```

**`scheduler.py` integration (after upsert_listings, before send_fleece_alerts):**
```python
new_items = await db.upsert_listings(listings)

if classifier.ready:
    results = await asyncio.gather(*[
        classifier.classify(item.get("image_url")) for item in new_items
    ])
    new_items = [item for item, r in zip(new_items, results) if r["want"]]

await send_fleece_alerts(new_items, webhook_url)
```

Using `asyncio.gather` classifies all new listings concurrently — no event loop blocking.

**Fallback behaviour:**
- Model file missing → `ready=False` → keyword filter still runs as before
- Image download fails → `want=True` (fail open, keyword filter still applies)
- Inference error → `want=True` (fail open)
- Scraper never breaks because model isn't deployed yet

---

## File Locations

```
C:\scraper\
├── classifier\
│   ├── __init__.py              ← FleeceClassifier (ONNX inference)
│   ├── seed_archive.py          ← seeds WANT from oldschooloutdoor.com
│   ├── seed_negatives.py        ← seeds DON'T WANT from DB
│   └── train.py                 ← local training script
│   └── fleece_classifier.onnx   ← committed after training
├── data\classifier\
│   ├── want\                    ← WANT images
│   ├── dont_want\               ← DON'T WANT images
│   ├── cache\                   ← inference image cache (gitignored)
│   └── labels.json              ← Tinder UI labels (gitignored)
├── ui\labeller.html             ← Tinder UI static page
└── main.py                      ← loads FleeceClassifier at startup
```

**`.gitignore` additions:**
```
data/classifier/        ← images + labels NOT committed (large binary files)
```
`classifier/fleece_classifier.onnx` is **tracked in git** (not gitignored). Committing it is the deployment mechanism — push triggers GitHub Actions → Watchtower redeploys NAS with the new model file.

---

## Success Criteria

- [ ] `seed_archive.py` downloads 70+ archive images into `data/classifier/want/`
- [ ] `seed_negatives.py` seeds 500+ non-Synchilla images into `data/classifier/dont_want/`
- [ ] `/labeller` UI shows images, accepts J/L/S keyboard input, saves labels, shows progress
- [ ] `train.py` runs to completion, exports valid `fleece_classifier.onnx`, prints val accuracy ≥ 80%
- [ ] `FleeceClassifier` loads ONNX, classifies a single image in < 500ms on NAS
- [ ] Scheduler passes only classifier-approved listings to Discord alerts
- [ ] When `fleece_classifier.onnx` is missing, scraper runs normally with keyword-only filter
- [ ] Existing tests still pass after integration
