"""Shared pytest fixtures."""

from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.dynamodb import get_dynamodb_client
from app.kafka.producer import get_kafka_send
from app.models.trade import Base

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def db_engine():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def mock_kafka_send():
    """AsyncMock replacing kafka send_trade so no broker is needed."""
    return AsyncMock()


@pytest.fixture
def mock_dynamodb_client():
    """AsyncMock replacing the aioboto3 DynamoDB client so no real table is needed."""
    client = AsyncMock()
    client.put_item = AsyncMock(return_value={})
    client.update_item = AsyncMock(return_value={})
    client.get_item = AsyncMock(return_value={})  # empty = item not found
    return client


@pytest.fixture
async def client(db_session, mock_kafka_send, mock_dynamodb_client):
    from app.main import app

    async def override_get_db():
        yield db_session

    def override_get_kafka_send():
        return mock_kafka_send

    def override_get_dynamodb_client():
        return mock_dynamodb_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_kafka_send] = override_get_kafka_send
    app.dependency_overrides[get_dynamodb_client] = override_get_dynamodb_client
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def trade_payload(
    trade_id: str = "T1",
    version: int = 1,
    maturity_date: date = None,
) -> dict:
    md = maturity_date or date.today() + timedelta(days=30)
    return {
        "trade_id": trade_id,
        "version": version,
        "counterparty_id": "CP-1",
        "book_id": "B1",
        "maturity_date": md.isoformat(),
        "created_date": date.today().isoformat(),
    }
