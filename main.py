"""
Main entry point — starts FastAPI (REST API + static UI) and APScheduler.
"""
import asyncio
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from db.database import Database
from scrapers import get_engine, ENGINES
from sites import SUPPORTED_SITES
import scheduler as sched

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/scraper.db")
PORT = int(os.getenv("PORT", "3000"))
HOST = os.getenv("HOST", "0.0.0.0")

# ─── Database ────────────────────────────────────────────────────────────────

db = Database(DATABASE_URL)

# ─── WebSocket connections ───────────────────────────────────────────────────

connected_clients: list[WebSocket] = []


async def broadcast(event: dict):
    """Send a JSON event to all connected WebSocket clients."""
    dead = []
    for ws in connected_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.remove(ws)


# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    sched.init_scheduler(db, broadcast_fn=broadcast)
    sched.start()
    logger.info(f"Marketplace Scraper running on port {PORT}")
    yield
    sched.stop()
    await db.close()


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Marketplace Scraper",
    description="Vinted · eBay · Depop scraper with multi-engine support",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Pydantic Models ─────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    search_term: str
    engine: str
    sites: list[str]
    interval_minutes: int = 60

    @field_validator("engine")
    @classmethod
    def validate_engine(cls, v):
        if v not in ENGINES:
            raise ValueError(f"engine must be one of {list(ENGINES.keys())}")
        return v

    @field_validator("sites")
    @classmethod
    def validate_sites(cls, v):
        for s in v:
            if s not in SUPPORTED_SITES:
                raise ValueError(f"site '{s}' must be one of {SUPPORTED_SITES}")
        return v


class JobUpdate(BaseModel):
    search_term: Optional[str] = None
    engine: Optional[str] = None
    sites: Optional[list[str]] = None
    active: Optional[bool] = None
    interval_minutes: Optional[int] = None


class BenchmarkRequest(BaseModel):
    search_term: str = "vintage jacket"
    engines: Optional[list[str]] = None   # None = all
    sites: Optional[list[str]] = None     # None = all


class LabelRequest(BaseModel):
    listing_id: int
    label: str

    @field_validator("label")
    @classmethod
    def validate_label(cls, v):
        if v not in ("want", "dont_want", "skip"):
            raise ValueError("label must be want, dont_want, or skip")
        return v


# ─── Labeller helpers ────────────────────────────────────────────────────────

LABELS_FILE = Path("data/classifier/labels.json")
_SYNCHILLA_KEYWORDS = {"synchilla", "snap-t", "snap t", "patagonia fleece"}


def _load_labels() -> dict:
    if LABELS_FILE.exists():
        return json.loads(LABELS_FILE.read_text())
    return {}


def _save_labels(labels: dict):
    LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LABELS_FILE.write_text(json.dumps(labels, indent=2))


def _is_synchilla(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _SYNCHILLA_KEYWORDS)


# ─── API Routes ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/api/stats")
async def get_stats():
    stats = await db.get_stats()
    stats["next_run"] = sched.next_run_time()
    return stats


# Jobs

@app.get("/api/jobs")
async def list_jobs(active_only: bool = False):
    jobs = await db.get_jobs(active_only=active_only)
    return [
        {
            "id": j.id,
            "search_term": j.search_term,
            "engine": j.engine,
            "sites": json.loads(j.sites) if isinstance(j.sites, str) else j.sites,
            "active": j.active,
            "interval_minutes": j.interval_minutes or 60,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "last_run": j.last_run.isoformat() if j.last_run else None,
            "run_count": j.run_count,
        }
        for j in jobs
    ]


@app.post("/api/jobs", status_code=201)
async def create_job(body: JobCreate):
    job = await db.create_job(body.search_term, body.engine, body.sites, body.interval_minutes)
    return {"id": job.id, "message": "Job created"}


@app.put("/api/jobs/{job_id}")
async def update_job(job_id: int, body: JobUpdate):
    updates = body.model_dump(exclude_none=True)
    job = await db.update_job(job_id, **updates)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"id": job.id, "message": "Job updated"}


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: int):
    ok = await db.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")


@app.post("/api/jobs/{job_id}/run")
async def trigger_job(job_id: int):
    try:
        await sched.run_job_now(job_id)
        return {"message": f"Job {job_id} triggered"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Listings

@app.get("/api/listings")
async def get_listings(
    job_id: Optional[int] = None,
    site: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    sort: str = Query("newest", pattern="^(newest|price_asc|price_desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    listings, total = await db.get_listings(
        job_id=job_id,
        site=site,
        since=since_dt,
        until=until_dt,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "listings": [l.to_dict() for l in listings],
    }


# Scraper Results

@app.get("/api/results")
async def get_results(
    job_id: Optional[int] = None,
    site: Optional[str] = None,
    engine: Optional[str] = None,
    success: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    results, total = await db.get_scraper_results(
        job_id=job_id, site=site, engine=engine, success=success,
        limit=limit, offset=offset,
    )
    return {"total": total, "offset": offset, "limit": limit, "results": results}


# Benchmark

@app.post("/api/benchmark")
async def run_benchmark(body: BenchmarkRequest):
    """Run all selected engines × sites and return results."""
    engines = body.engines or list(ENGINES.keys())
    sites = body.sites or SUPPORTED_SITES

    # Validate
    for e in engines:
        if e not in ENGINES:
            raise HTTPException(400, f"Unknown engine: {e}")
    for s in sites:
        if s not in SUPPORTED_SITES:
            raise HTTPException(400, f"Unknown site: {s}")

    async def _run_single(engine_name, site):
        engine = get_engine(engine_name)
        result = await engine.scrape(site, body.search_term)
        items_new, _ = await db.upsert_listings(result.listings, job_id=None)
        r = await db.save_scraper_result({
            "job_id": None,
            "engine": engine_name,
            "site": site,
            "run_at": datetime.utcnow(),
            "duration_ms": result.duration_ms,
            "items_found": len(result.listings),
            "items_new": items_new,
            "success": result.success,
            "error_message": result.error_message,
            "memory_mb": result.memory_mb,
        })
        return r.to_dict()

    tasks = [_run_single(e, s) for e in engines for s in sites]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for item in results:
        if isinstance(item, Exception):
            output.append({"success": False, "error_message": str(item)})
        else:
            output.append(item)

    return {"results": output}


# Metadata

@app.get("/api/engines")
async def list_engines():
    descriptions = {
        "playwright": "Playwright (Python) — primary engine for JS-heavy sites. Stealth mode enabled.",
        "beautifulsoup": "BeautifulSoup + httpx — fast static-page parser. Best for eBay.",
        "scrapy": "Scrapy — high-volume async crawling with auto-throttle. Best for scale.",
        "puppeteer": "Puppeteer (Node.js) — Chrome automation with stealth plugin. Fallback for Playwright.",
        "crawl4ai": "Crawl4AI — LLM-friendly Markdown output. Useful for piping into AI categorisation.",
    }
    recommended = {
        "vinted": "playwright",
        "ebay": "beautifulsoup",
        "depop": "playwright",
    }
    return {
        "engines": [
            {"id": k, "description": descriptions[k], "recommended_for": [
                site for site, eng in recommended.items() if eng == k
            ]}
            for k in ENGINES
        ],
        "recommended": recommended,
    }


@app.get("/api/sites")
async def list_sites():
    return {"sites": SUPPORTED_SITES}


# ─── Settings ────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    discord_webhook_url: Optional[str] = None


@app.get("/api/settings")
async def get_settings():
    url = await db.get_setting("discord_webhook_url")
    return {
        "discord_webhook_url": url or "",
        "discord_configured": bool(url and url.strip()),
    }


@app.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    if body.discord_webhook_url is not None:
        await db.set_setting("discord_webhook_url", body.discord_webhook_url.strip())
    return {"message": "Settings saved"}


@app.post("/api/discord/test")
async def test_discord():
    from discord_webhook import get_webhook_url, send_test_alert
    url = await get_webhook_url(db)
    if not url:
        raise HTTPException(status_code=400, detail="No Discord webhook URL configured")
    ok, message = await send_test_alert(url)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    return {"message": message}


# ─── Labeller API ────────────────────────────────────────────────────────────

@app.get("/api/labeller/progress")
async def labeller_progress():
    labels = _load_labels()
    want = sum(1 for v in labels.values() if v.get("label") == "want" and v.get("source") == "user")
    dont_want = sum(1 for v in labels.values() if v.get("label") == "dont_want" and v.get("source") == "user")
    listings, _ = await db.get_listings(limit=10000, offset=0)
    synchilla_ids = {l.id for l in listings if _is_synchilla(l.title or "")}
    return {"labelled": want + dont_want, "total": len(synchilla_ids),
            "want_count": want, "dont_want_count": dont_want}


@app.get("/api/labeller/next")
async def labeller_next():
    labels = _load_labels()
    labelled_ids = {v.get("listing_id") for v in labels.values() if v.get("source") == "user"}
    listings, _ = await db.get_listings(limit=10000, offset=0)
    candidates = [
        l for l in listings
        if _is_synchilla(l.title or "")
        and l.image_url
        and l.id not in labelled_ids
    ]
    if not candidates:
        return {"done": True, "remaining": 0}
    item = random.choice(candidates)
    return {
        "id": item.id, "image_url": item.image_url,
        "title": item.title, "price": item.price,
        "site": item.site, "size": item.size,
        "condition": item.condition,
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


# ─── WebSocket ───────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            # Keep alive — client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.remove(websocket)


# ─── PWA Static Files ────────────────────────────────────────────────────────

_UI_ROOT = os.path.join(os.path.dirname(__file__), "ui")


@app.get("/manifest.json", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(
        os.path.join(_UI_ROOT, "manifest.json"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker():
    return FileResponse(
        os.path.join(_UI_ROOT, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/icons/{filename}", include_in_schema=False)
async def pwa_icon(filename: str):
    # Prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400)
    path = os.path.join(_UI_ROOT, "icons", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404)
    ext = filename.rsplit(".", 1)[-1].lower()
    media = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
    return FileResponse(path, media_type=media.get(ext, "application/octet-stream"))


# ─── Static UI ───────────────────────────────────────────────────────────────

UI_DIR = os.path.join(os.path.dirname(__file__), "ui", "dist")
if os.path.isdir(UI_DIR):
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
else:
    # Fallback: serve the single-file UI
    FALLBACK_UI = os.path.join(os.path.dirname(__file__), "ui", "index.html")

    @app.get("/")
    async def root():
        if os.path.exists(FALLBACK_UI):
            return FileResponse(FALLBACK_UI)
        return {"message": "Marketplace Scraper API", "docs": "/docs"}


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
