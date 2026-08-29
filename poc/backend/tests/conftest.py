import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db

os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("OPENAI_API_KEY", "")

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://poc:poc@localhost:5433/claim_checker"
)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_shared_db_engine():
    """app.db.engine is a module-level singleton (correct for production's one long-lived event
    loop), but pytest-asyncio gives each test its own loop — its pooled connections are bound to
    the loop that created them, so a connection from a previous test's loop is unusable here.
    Disposing after every test forces a fresh pool on next use, bound to whatever loop is live."""
    yield
    await app.db.engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """One connection per test, wrapped in a transaction that's always rolled back."""
    engine = create_async_engine(TEST_DATABASE_URL)
    connection = await engine.connect()
    transaction = await connection.begin()

    session_factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    session = session_factory()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
def fixtures_dir():
    return os.path.join(os.path.dirname(__file__), "fixtures")
