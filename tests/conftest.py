"""Set env vars before any app module is imported — must be at the top level."""
import os

# Point to an in-memory SQLite DB for all API tests
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_scraper.db")
os.environ.setdefault("PORT", "3001")
