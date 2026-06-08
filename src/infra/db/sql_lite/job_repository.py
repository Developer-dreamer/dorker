import json
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import Boolean, DateTime, Float, String, Text, TypeDecorator, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.domain.model.job import Job


class Base(DeclarativeBase):
    pass

class SQLiteJSON(TypeDecorator):
    """Safely handles JSON serialization/deserialization for SQLite text storage."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value: str | None, dialect: Any) -> Any:
        if value is None:
            return None
        return json.loads(value)

class JobEntity(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ats_type: Mapped[str] = mapped_column(String(50), nullable=False)
    ats_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    employment_type: Mapped[str | None] = mapped_column(String(50), default="FULL_TIME", nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    application_questions: Mapped[list[dict[str, Any]] | None] = mapped_column(SQLiteJSON, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)




class JobRepository:
    """Production-ready SQLite data repository executing asynchronous cross-compatible operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_compact_job(self, domain_job: Job) -> int:
        """Saves or updates a CompactJob model instance on SQLite using cross-dialect patterns."""
        # 1. Look up if the entity already exists (Atomic SQLite replication of PostgreSQL indexes)
        stmt = select(JobEntity).where(
            JobEntity.ats_type == domain_job.ats_type,
            JobEntity.ats_id == domain_job.ats_id
        )
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()

        if entity:
            # Update mutable parameters manually
            entity.url = str(domain_job.url)
            entity.apply_url = str(domain_job.apply_url) if domain_job.apply_url else None
            entity.title = domain_job.title
            entity.location = domain_job.location
            entity.is_remote = domain_job.is_remote
            entity.description = domain_job.description
            entity.salary_min = domain_job.salary_min
            entity.salary_max = domain_job.salary_max
            entity.application_questions = domain_job.application_questions
            entity.fetched_at = domain_job.fetched_at
        else:
            # Construct a brand new record
            entity = JobEntity(
                ats_type=domain_job.ats_type,
                ats_id=domain_job.ats_id,
                url=str(domain_job.url),
                apply_url=str(domain_job.apply_url) if domain_job.apply_url else None,
                title=domain_job.title,
                company_slug=domain_job.company_slug,
                location=domain_job.location,
                is_remote=domain_job.is_remote,
                employment_type=domain_job.employment_type,
                description=domain_job.description,
                salary_min=domain_job.salary_min,
                salary_max=domain_job.salary_max,
                salary_currency=domain_job.salary_currency,
                application_questions=domain_job.application_questions,
                posted_at=domain_job.posted_at,
                fetched_at=domain_job.fetched_at,
            )
            self.session.add(entity)

        await self.session.flush()  # Ensures the entity obtains its autoincremented ID block
        return entity.id

    async def retrieve_by_id(self, job_id: int) -> Job | None:
        """Fetches a record from SQLite state and returns it safely mapped to a domain CompactJob object."""
        stmt = select(JobEntity).where(JobEntity.id == job_id)
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()

        if not entity:
            return None

        return self._to_domain(entity)

    async def fetch_matching_pool(self, target_keywords: list[str], limit: int = 300) -> Sequence[Job]:
        """Performs optimized title substring pre-filtering compatible with standard SQLite engines."""
        # Convert explicit list values to SQL LIKE instructions array constructs
        like_filters = [JobEntity.title.like(f"%{kw}%") for kw in target_keywords]

        stmt = select(JobEntity).where(
            JobEntity.is_remote == True,
            or_(*like_filters)
        ).order_by(JobEntity.fetched_at.desc()).limit(limit)

        result = await self.session.execute(stmt)
        entities = result.scalars().all()
        return [self._to_domain(e) for e in entities]

    @staticmethod
    def _to_domain(entity: JobEntity) -> Job:
        return Job(
            ats_type=entity.ats_type,
            ats_id=entity.ats_id,
            url=entity.url,
            apply_url=entity.apply_url,
            title=entity.title,
            company_slug=entity.company_slug,
            location=entity.location,
            is_remote=entity.is_remote,
            employment_type=entity.employment_type,
            description=entity.description,
            salary_min=entity.salary_min,
            salary_max=entity.salary_max,
            salary_currency=entity.salary_currency,
            application_questions=entity.application_questions,
            posted_at=entity.posted_at,
            fetched_at=entity.fetched_at,
        )
