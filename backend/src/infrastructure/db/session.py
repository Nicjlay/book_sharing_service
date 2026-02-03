from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# В будущем вынесем это в .env
DATABASE_URL = "postgresql+asyncpg://user:pass@db:5432/library_db"

engine = create_async_engine(DATABASE_URL, echo=True) # echo=True покажет SQL в логах
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Зависимость для FastAPI
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session