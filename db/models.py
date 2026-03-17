from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Text,
    ForeignKey, UniqueConstraint, event
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class AppConfig(Base):
    __tablename__ = "app_config"

    key   = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    search_term = Column(String, nullable=False)
    engine = Column(
        String,
        nullable=False,
        # CHECK constraint handled in __table_args__
    )
    sites = Column(Text, nullable=False)  # JSON array: ["vinted","ebay","depop"]
    active = Column(Boolean, default=True)
    interval_minutes = Column(Integer, default=60)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_run = Column(DateTime, nullable=True)
    run_count = Column(Integer, default=0)

    listings = relationship("Listing", back_populates="job", cascade="all, delete-orphan")
    scraper_results = relationship("ScraperResult", back_populates="job", cascade="all, delete-orphan")

    VALID_ENGINES = {"playwright", "scrapy", "beautifulsoup", "puppeteer", "crawl4ai"}

    def __repr__(self):
        return f"<Job id={self.id} term='{self.search_term}' engine={self.engine}>"


class Listing(Base):
    __tablename__ = "listings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    site = Column(String, nullable=False)
    listing_id = Column(String, nullable=False)  # platform-native ID
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    currency = Column(String, default="GBP")
    brand = Column(String, nullable=True)
    size = Column(String, nullable=True)
    condition = Column(String, nullable=True)
    seller = Column(String, nullable=True)
    image_url = Column(Text, nullable=True)
    listing_url = Column(Text, nullable=True)
    raw_json = Column(Text, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="listings")

    __table_args__ = (
        UniqueConstraint("site", "listing_id", name="uq_site_listing"),
    )

    def __repr__(self):
        return f"<Listing id={self.id} site={self.site} listing_id={self.listing_id}>"

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "site": self.site,
            "listing_id": self.listing_id,
            "title": self.title,
            "description": self.description,
            "price": self.price,
            "currency": self.currency,
            "brand": self.brand,
            "size": self.size,
            "condition": self.condition,
            "seller": self.seller,
            "image_url": self.image_url,
            "listing_url": self.listing_url,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
        }


class ScraperResult(Base):
    __tablename__ = "scraper_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    engine = Column(String, nullable=True)
    site = Column(String, nullable=True)
    run_at = Column(DateTime, default=datetime.utcnow)
    duration_ms = Column(Integer, nullable=True)
    items_found = Column(Integer, nullable=True)
    items_new = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=True)
    error_message = Column(Text, nullable=True)
    memory_mb = Column(Float, nullable=True)

    job = relationship("Job", back_populates="scraper_results")

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "engine": self.engine,
            "site": self.site,
            "run_at": self.run_at.isoformat() if self.run_at else None,
            "duration_ms": self.duration_ms,
            "items_found": self.items_found,
            "items_new": self.items_new,
            "success": self.success,
            "error_message": self.error_message,
            "memory_mb": self.memory_mb,
        }
