"""API integration tests using FastAPI TestClient."""
import json
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


# We need to patch the database before importing app
@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test.db")
    import os
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["PORT"] = "3001"

    from main import app, db
    import asyncio
    asyncio.get_event_loop().run_until_complete(db.init())

    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_job(client):
    r = client.post("/api/jobs", json={
        "search_term": "vintage jacket",
        "engine": "playwright",
        "sites": ["vinted", "ebay"],
    })
    assert r.status_code == 201
    assert "id" in r.json()


def test_list_jobs(client):
    client.post("/api/jobs", json={"search_term": "shoes", "engine": "beautifulsoup", "sites": ["ebay"]})
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_update_job(client):
    r = client.post("/api/jobs", json={"search_term": "hat", "engine": "playwright", "sites": ["vinted"]})
    job_id = r.json()["id"]
    r2 = client.put(f"/api/jobs/{job_id}", json={"active": False})
    assert r2.status_code == 200


def test_delete_job(client):
    r = client.post("/api/jobs", json={"search_term": "bag", "engine": "playwright", "sites": ["depop"]})
    job_id = r.json()["id"]
    r2 = client.delete(f"/api/jobs/{job_id}")
    assert r2.status_code == 204
    r3 = client.get("/api/jobs")
    ids = [j["id"] for j in r3.json()]
    assert job_id not in ids


def test_invalid_engine(client):
    r = client.post("/api/jobs", json={"search_term": "test", "engine": "invalid", "sites": ["ebay"]})
    assert r.status_code == 422


def test_invalid_site(client):
    r = client.post("/api/jobs", json={"search_term": "test", "engine": "playwright", "sites": ["amazon"]})
    assert r.status_code == 422


def test_get_listings_empty(client):
    r = client.get("/api/listings")
    assert r.status_code == 200
    data = r.json()
    assert "listings" in data
    assert data["total"] == 0


def test_list_engines(client):
    r = client.get("/api/engines")
    assert r.status_code == 200
    data = r.json()
    assert "engines" in data
    engine_ids = [e["id"] for e in data["engines"]]
    assert "playwright" in engine_ids
    assert "beautifulsoup" in engine_ids


def test_list_sites(client):
    r = client.get("/api/sites")
    assert r.status_code == 200
    assert "sites" in r.json()
    assert "vinted" in r.json()["sites"]


def test_stats(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert "total_jobs" in data
