import os

from dotenv import load_dotenv, find_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

load_dotenv(find_dotenv())

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")

# Fix #14: echo=True в продакшне засоряет логи и нагружает I/O.
# Управляется переменной SQL_ECHO=true в .env (по умолчанию выключен).
_sql_echo = os.getenv("SQL_ECHO", "false").lower() == "true"

engine = create_async_engine(DATABASE_URL, echo=_sql_echo)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session