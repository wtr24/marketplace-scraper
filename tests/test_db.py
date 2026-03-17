"""Integration tests for the Database layer using an in-memory SQLite DB."""
import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from db.database import Database

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_and_get_job(db):
    job = await db.create_job("vintage jacket", "playwright", ["vinted", "ebay"])
    assert job.id is not None
    assert job.search_term == "vintage jacket"
    assert job.engine == "playwright"
    assert job.active is True

    fetched = await db.get_job(job.id)
    assert fetched.id == job.id


@pytest.mark.asyncio
async def test_list_jobs(db):
    await db.create_job("term1", "playwright", ["vinted"])
    await db.create_job("term2", "beautifulsoup", ["ebay"])
    jobs = await db.get_jobs()
    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_update_job(db):
    job = await db.create_job("test", "playwright", ["vinted"])
    updated = await db.update_job(job.id, active=False, search_term="new term")
    assert updated.active is False
    assert updated.search_term == "new term"


@pytest.mark.asyncio
async def test_delete_job(db):
    job = await db.create_job("delete me", "playwright", ["vinted"])
    ok = await db.delete_job(job.id)
    assert ok is True
    fetched = await db.get_job(job.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_upsert_listings_deduplication(db):
    job = await db.create_job("test", "playwright", ["ebay"])
    listings = [
        {
            "site": "ebay",
            "listing_id": "111222333",
            "title": "Test item",
            "price": 10.0,
            "currency": "GBP",
            "listing_url": "https://ebay.co.uk/itm/111222333",
        }
    ]

    count1, new_items1 = await db.upsert_listings(listings, job_id=job.id)
    assert count1 == 1
    assert len(new_items1) == 1

    # Insert same listing again — should be ignored
    count2, new_items2 = await db.upsert_listings(listings, job_id=job.id)
    assert count2 == 0
    assert new_items2 == []

    all_listings, total = await db.get_listings(job_id=job.id)
    assert total == 1


@pytest.mark.asyncio
async def test_get_listings_filters(db):
    job = await db.create_job("test", "playwright", ["vinted", "ebay"])
    await db.upsert_listings([
        {"site": "vinted", "listing_id": "1", "title": "A", "price": 5.0, "currency": "GBP"},
        {"site": "ebay",   "listing_id": "2", "title": "B", "price": 15.0, "currency": "GBP"},
        {"site": "vinted", "listing_id": "3", "title": "C", "price": 25.0, "currency": "GBP"},
    ], job_id=job.id)

    vinted_only, total = await db.get_listings(site="vinted")
    assert total == 2

    cheap, total = await db.get_listings(max_price=10.0)
    assert total == 1
    assert cheap[0].price == 5.0


@pytest.mark.asyncio
async def test_save_scraper_result(db):
    from datetime import datetime
    job = await db.create_job("test", "playwright", ["vinted"])
    sr = await db.save_scraper_result({
        "job_id": job.id,
        "engine": "playwright",
        "site": "vinted",
        "run_at": utcnow(),
        "duration_ms": 3500,
        "items_found": 12,
        "items_new": 5,
        "success": True,
        "error_message": None,
        "memory_mb": 45.2,
    })
    assert sr.id is not None
    assert sr.items_found == 12


@pytest.mark.asyncio
async def test_stats(db):
    stats = await db.get_stats()
    assert "total_jobs" in stats
    assert "listings_today" in stats
    assert "recent_listings" in stats
