# Marketplace Scraper

A modular, containerised web scraper that monitors **Vinted**, **eBay**, and **Depop** for new listings, with a browser-based management UI.

## Features

- **5 scraper engines**: Playwright, BeautifulSoup, Scrapy, Puppeteer, Crawl4AI
- **3 marketplaces**: Vinted, eBay, Depop
- **Auto-deduplication** via `UNIQUE(site, listing_id)` constraint
- **5-minute polling** via APScheduler
- **Management UI** — dashboard, job manager, benchmark runner, listings browser
- **Docker-ready** — runs on Synology / QNAP / TrueNAS NAS devices
- **GitHub Actions CI/CD** — builds `linux/amd64` + `linux/arm64` images

---

## Quick Start

### Local (Python)

```bash
git clone https://github.com/YOUR_USERNAME/marketplace-scraper
cd marketplace-scraper

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps

cp .env.example .env
mkdir -p data logs

python main.py
# → http://localhost:3000
```

### Docker Compose (NAS deployment)

```bash
# 1. SSH into the NAS and create the app folder
ssh admin@192.168.0.18
mkdir -p /volume1/docker/marketplace-scraper/data /volume1/docker/marketplace-scraper/logs

# 2. Copy docker-compose.yml to the NAS (from your machine)
scp docker-compose.yml admin@192.168.0.18:/volume1/docker/marketplace-scraper/

# 3. Start (image pulled from Docker Hub automatically)
cd /volume1/docker/marketplace-scraper
docker compose up -d

# 4. Open the UI
# http://192.168.0.18:3003
```

### Build locally

```bash
docker build -t marketplace-scraper .
docker run -p 3000:3000 -v $(pwd)/data:/data marketplace-scraper
# or with nginx:
docker compose up -d   # nginx on http://localhost:3003
```

---

## Architecture

```
nginx (port 3003, external)
  └── proxy_pass → FastAPI (port 3000, internal)
        ├── Static UI  ← ui/index.html
        ├── REST API   ← /api/*
        ├── WebSocket  ← /ws
        ├── APScheduler (every 5 min)
        │     └── Scraper Engine Registry
        │           ├── Playwright  ← primary (Vinted, Depop)
        │           ├── BeautifulSoup ← eBay
        │           ├── Scrapy
        │           ├── Puppeteer
        │           └── Crawl4AI
        └── SQLite / PostgreSQL
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/stats` | Dashboard stats |
| GET | `/api/jobs` | List all jobs |
| POST | `/api/jobs` | Create a job |
| PUT | `/api/jobs/:id` | Update a job |
| DELETE | `/api/jobs/:id` | Delete a job |
| POST | `/api/jobs/:id/run` | Trigger job immediately |
| GET | `/api/listings` | Browse listings (filterable) |
| GET | `/api/results` | Scraper run history |
| POST | `/api/benchmark` | Run engine benchmark |
| GET | `/api/engines` | List engines + recommendations |
| GET | `/api/sites` | List supported sites |
| WS | `/ws` | WebSocket live feed |

### Create job example

```json
POST /api/jobs
{
  "search_term": "vintage Levi jacket",
  "engine": "playwright",
  "sites": ["vinted", "ebay", "depop"]
}
```

### Benchmark example

```json
POST /api/benchmark
{
  "search_term": "nike trainers",
  "engines": ["playwright", "beautifulsoup"],
  "sites": ["vinted", "ebay"]
}
```

---

## Scraper Engines

| Engine | Language | Best for | Notes |
|--------|----------|----------|-------|
| **Playwright** | Python | Vinted, Depop | Primary — stealth mode, JS rendering |
| **BeautifulSoup** | Python | eBay | Fast, static HTML parsing |
| **Scrapy** | Python | Scale | Auto-throttle, async crawl |
| **Puppeteer** | Node.js | Fallback | Chrome + stealth plugin |
| **Crawl4AI** | Python | LLM pipeline | Markdown output |

### Recommended engines per site

| Site | Primary | Fallback |
|------|---------|----------|
| Vinted | Playwright | Puppeteer |
| eBay | BeautifulSoup | Playwright |
| Depop | Playwright | Crawl4AI |

---

## Anti-bot Mitigations

Applied to every engine:
- 10+ rotating User-Agent strings
- Random 2–8s delay between requests
- Stealth mode (disables `navigator.webdriver`)
- `Accept-Language` / `Accept-Encoding` headers mimicking real browsers
- Playwright: `--no-sandbox`, `--disable-blink-features=AutomationControlled`
- Scrapy: `AUTOTHROTTLE_ENABLED`, `DOWNLOAD_DELAY=3`
- Crawl4AI: `simulate_user=True`, `override_navigator=True`

---

## Database Schema

```sql
jobs           — scraping jobs (search term + engine + sites)
listings       — deduplicated scraped items
scraper_results — per-run metrics (speed, items, errors)
```

Default: SQLite at `/data/scraper.db`. Switch to Postgres by setting:
```
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

---

## NAS Deployment

NAS IP: `192.168.0.18` — UI at `http://192.168.0.18:3003` (nginx → scraper:3000)

```bash
# First-time setup on NAS
ssh admin@192.168.0.18
mkdir -p /volume1/docker/marketplace-scraper/{data,logs}
cd /volume1/docker/marketplace-scraper
# copy docker-compose.yml here, then:
docker compose up -d
```

**Auto-deploy**: Watchtower polls Docker Hub every 30s. Push to `main` → Actions build & push `:latest` → Watchtower redeploys automatically.

**Manual rollback**: GitHub Actions → Rollback workflow → enter SHA.

---

## Development

```bash
# Run tests
pytest tests/ -v

# Run with auto-reload
uvicorn main:app --reload --port 3000

# Lint
ruff check .
```

---

## Project Structure

```
marketplace-scraper/
├── .github/workflows/docker-publish.yml  # CI/CD
├── scrapers/
│   ├── base.py                # Abstract BaseScraper + UA rotation
│   ├── playwright_scraper.py
│   ├── scrapy_scraper.py
│   ├── bs4_scraper.py
│   ├── puppeteer_scraper.py
│   └── crawl4ai_scraper.py
├── sites/
│   ├── vinted.py              # Selectors + normalise()
│   ├── ebay.py
│   └── depop.py
├── db/
│   ├── models.py              # SQLAlchemy ORM models
│   └── database.py            # Async DB operations
├── nginx/
│   ├── Dockerfile             # nginx:alpine + config
│   └── nginx.conf             # Proxy to scraper:3000, WebSocket upgrade
├── ui/index.html              # Single-file management UI
├── tests/
│   ├── test_sites.py
│   ├── test_db.py
│   └── test_api.py
├── scheduler.py               # APScheduler setup
├── main.py                    # FastAPI entry point
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```
