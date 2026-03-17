# app/core/database.py — Connection pooling optimized for 2000+ concurrent users

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings


# ── ENGINE (pool_size + max_overflow tuned for high concurrency) ──

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=settings.DB_POOL_SIZE,       # 20 persistent connections
    max_overflow=settings.DB_MAX_OVERFLOW,  # 40 burst connections
    pool_pre_ping=True,                    # detect stale connections
    pool_recycle=1800,                     # recycle connections every 30 min
)


# ── SESSION FACTORY ──

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── BASE ──

class Base(DeclarativeBase):
    pass


# ── DEPENDENCY ──

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
