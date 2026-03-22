# tests/test_labeller_api.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def test_labeller_progress_returns_counts(client):
    resp = client.get("/api/labeller/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert "labelled" in data
    assert "total" in data
    assert "want_count" in data
    assert "dont_want_count" in data


def test_labeller_next_returns_listing_or_done(client):
    resp = client.get("/api/labeller/next")
    assert resp.status_code == 200
    data = resp.json()
    assert "done" in data or "image_url" in data


def test_labeller_label_invalid_value(client):
    resp = client.post("/api/labeller/label", json={"listing_id": 1, "label": "invalid"})
    assert resp.status_code == 422
