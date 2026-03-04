import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_dotenv_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_dotenv_path)

from utils import get_env_int

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")

_sql_echo = os.getenv("SQL_ECHO", "false").lower() == "true"

_pool_size    = get_env_int("DB_POOL_SIZE",    default=10,   min_val=1,  max_val=100)
_max_overflow = get_env_int("DB_MAX_OVERFLOW", default=20,   min_val=0,  max_val=200)
_pool_timeout = get_env_int("DB_POOL_TIMEOUT", default=30,   min_val=1,  max_val=120)
_pool_recycle = get_env_int("DB_POOL_RECYCLE", default=1800, min_val=60, max_val=7200)

engine = create_async_engine(
    DATABASE_URL,
    echo=_sql_echo,
    # pool_pre_ping: проверяет живость соединения перед использованием.
    # Критично для Docker: после рестарта PostgreSQL idle-коннекты становятся
    # «мёртвыми» (TCP half-open). Без этого — OperationalError → 500.
    pool_pre_ping=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_recycle=_pool_recycle,
    pool_timeout=_pool_timeout,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """
    FastAPI dependency: создаёт сессию на время запроса.
    При исключении SQLAlchemy откатит незакоммиченные изменения при close().
    """
    async with AsyncSessionLocal() as session:
        yield session


async def dispose_engine():
    """Вызывается при shutdown для корректного закрытия пула соединений."""
    await engine.dispose()
    logger.info("DB connection pool disposed")
