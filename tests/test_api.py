"""API integration tests using FastAPI TestClient.

conftest.py sets DATABASE_URL before any app import happens.
TestClient handles the lifespan (db.init + scheduler) — no manual event loop calls.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app
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
    assert len(r.json()) >= 1


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
    assert "total" in data


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
