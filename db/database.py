import json
import logging
from datetime import datetime, timedelta, timezone

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
from typing import Any, Optional

from sqlalchemy import select, func, desc, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.models import Base, Job, Listing, ScraperResult

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, database_url: str):
        # Convert sync sqlite URL to async
        if database_url.startswith("sqlite:///"):
            async_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        elif database_url.startswith("postgresql://"):
            async_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        else:
            async_url = database_url

        self.engine = create_async_engine(async_url, echo=False)
        self.SessionLocal = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Migrate: add description column for existing databases
            try:
                await conn.execute(text("ALTER TABLE listings ADD COLUMN description TEXT"))
            except Exception:
                pass  # column already exists
            try:
                await conn.execute(text("ALTER TABLE jobs ADD COLUMN interval_minutes INTEGER DEFAULT 60"))
            except Exception:
                pass  # column already exists
        logger.info("Database initialised")

    async def close(self):
        await self.engine.dispose()

    # ─── Jobs ───────────────────────────────────────────────────────────────

    async def create_job(self, search_term: str, engine: str, sites: list[str], interval_minutes: int = 60) -> Job:
        async with self.SessionLocal() as session:
            job = Job(
                search_term=search_term,
                engine=engine,
                sites=json.dumps(sites),
                interval_minutes=interval_minutes,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    async def get_jobs(self, active_only: bool = False) -> list[Job]:
        async with self.SessionLocal() as session:
            stmt = select(Job).order_by(desc(Job.created_at))
            if active_only:
                stmt = stmt.where(Job.active == True)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_job(self, job_id: int) -> Optional[Job]:
        async with self.SessionLocal() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            return result.scalar_one_or_none()

    async def update_job(self, job_id: int, **kwargs) -> Optional[Job]:
        async with self.SessionLocal() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                return None
            for key, value in kwargs.items():
                if key == "sites" and isinstance(value, list):
                    value = json.dumps(value)
                setattr(job, key, value)
            await session.commit()
            await session.refresh(job)
            return job

    async def delete_job(self, job_id: int) -> bool:
        async with self.SessionLocal() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                return False
            await session.delete(job)
            await session.commit()
            return True

    async def mark_job_run(self, job_id: int):
        async with self.SessionLocal() as session:
            result = await session.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                job.last_run = _utcnow()
                job.run_count = (job.run_count or 0) + 1
                await session.commit()

    # ─── Listings ───────────────────────────────────────────────────────────

    async def upsert_listings(
        self, listings: list[dict[str, Any]], job_id: Optional[int] = None
    ) -> int:
        """Insert listings, ignoring duplicates. Returns number of new rows inserted."""
        if not listings:
            return 0

        new_count = 0
        async with self.SessionLocal() as session:
            for item in listings:
                # Skip rows with no listing_id — they'd all conflict on the same dedup key
                if not item.get("listing_id"):
                    continue
                stmt = sqlite_insert(Listing).values(
                    job_id=job_id,
                    site=item.get("site"),
                    listing_id=item.get("listing_id"),
                    title=item.get("title"),
                    description=item.get("description"),
                    price=item.get("price"),
                    currency=item.get("currency", "GBP"),
                    brand=item.get("brand"),
                    size=item.get("size"),
                    condition=item.get("condition"),
                    seller=item.get("seller"),
                    image_url=item.get("image_url"),
                    listing_url=item.get("listing_url"),
                    raw_json=json.dumps(item.get("raw", {})),
                    scraped_at=_utcnow(),
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["site", "listing_id"])
                result = await session.execute(stmt)
                new_count += result.rowcount
            await session.commit()
        return new_count

    async def get_listings(
        self,
        job_id: Optional[int] = None,
        site: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort: str = "newest",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Listing], int]:
        async with self.SessionLocal() as session:
            stmt = select(Listing)
            count_stmt = select(func.count()).select_from(Listing)

            if job_id:
                stmt = stmt.where(Listing.job_id == job_id)
                count_stmt = count_stmt.where(Listing.job_id == job_id)
            if site:
                stmt = stmt.where(Listing.site == site)
                count_stmt = count_stmt.where(Listing.site == site)
            if since:
                stmt = stmt.where(Listing.scraped_at >= since)
                count_stmt = count_stmt.where(Listing.scraped_at >= since)
            if until:
                stmt = stmt.where(Listing.scraped_at <= until)
                count_stmt = count_stmt.where(Listing.scraped_at <= until)
            if min_price is not None:
                stmt = stmt.where(Listing.price >= min_price)
                count_stmt = count_stmt.where(Listing.price >= min_price)
            if max_price is not None:
                stmt = stmt.where(Listing.price <= max_price)
                count_stmt = count_stmt.where(Listing.price <= max_price)

            if sort == "newest":
                stmt = stmt.order_by(desc(Listing.scraped_at))
            elif sort == "price_asc":
                stmt = stmt.order_by(Listing.price.asc().nullslast())
            elif sort == "price_desc":
                stmt = stmt.order_by(desc(Listing.price))

            total_result = await session.execute(count_stmt)
            total = total_result.scalar_one()

            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all()), total

    # ─── Scraper Results ────────────────────────────────────────────────────

    async def save_scraper_result(self, result: dict[str, Any]) -> ScraperResult:
        async with self.SessionLocal() as session:
            sr = ScraperResult(**result)
            session.add(sr)
            await session.commit()
            await session.refresh(sr)
            return sr

    async def get_scraper_results(
        self, job_id: Optional[int] = None, limit: int = 100
    ) -> list[ScraperResult]:
        async with self.SessionLocal() as session:
            stmt = select(ScraperResult).order_by(desc(ScraperResult.run_at)).limit(limit)
            if job_id:
                stmt = stmt.where(ScraperResult.job_id == job_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ─── Dashboard Stats ────────────────────────────────────────────────────

    async def get_stats(self) -> dict[str, Any]:
        async with self.SessionLocal() as session:
            today = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            one_hour_ago = _utcnow() - timedelta(hours=1)

            total_jobs = await session.scalar(select(func.count()).select_from(Job))
            active_jobs = await session.scalar(
                select(func.count()).select_from(Job).where(Job.active == True)
            )
            total_listings_today = await session.scalar(
                select(func.count())
                .select_from(Listing)
                .where(Listing.scraped_at >= today)
            )
            new_last_hour = await session.scalar(
                select(func.count())
                .select_from(Listing)
                .where(Listing.scraped_at >= one_hour_ago)
            )
            total_listings = await session.scalar(
                select(func.count()).select_from(Listing)
            )
            recent_errors = await session.scalar(
                select(func.count())
                .select_from(ScraperResult)
                .where(
                    ScraperResult.success == False,
                    ScraperResult.run_at >= today,
                )
            )

            # Recent listings for live feed
            recent_stmt = (
                select(Listing)
                .order_by(desc(Listing.scraped_at))
                .limit(10)
            )
            recent_result = await session.execute(recent_stmt)
            recent_listings = [l.to_dict() for l in recent_result.scalars().all()]

            return {
                "total_jobs": total_jobs,
                "active_jobs": active_jobs,
                "total_listings": total_listings,
                "listings_today": total_listings_today,
                "new_last_hour": new_last_hour,
                "errors_today": recent_errors,
                "recent_listings": recent_listings,
            }
