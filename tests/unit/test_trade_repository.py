from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.trade import Base, Trade
from app.repositories.trade_repository import TradeRepository

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def make_trade_model(
    trade_id: str = "T1",
    version: int = 1,
    maturity_date: date = None,
) -> Trade:
    return Trade(
        trade_id=trade_id,
        version=version,
        counterparty_id="CP-1",
        book_id="B1",
        maturity_date=maturity_date or date.today() + timedelta(days=30),
        created_date=date.today(),
    )


class TestTradeRepository:
    async def test_upsert_insert_new_trade(self, session):
        repo = TradeRepository(session)
        trade = make_trade_model()
        await repo.upsert(trade)
        await session.commit()

        result = await repo.get_by_id_and_version("T1", 1)
        assert result is not None
        assert result.trade_id == "T1"
        assert result.version == 1

    async def test_upsert_updates_existing_trade(self, session):
        repo = TradeRepository(session)
        trade_v1 = make_trade_model(
            trade_id="T2", version=1, maturity_date=date.today() + timedelta(days=10)
        )
        await repo.upsert(trade_v1)
        await session.commit()

        # Same trade_id + version but different counterparty → should update
        trade_v1_updated = make_trade_model(
            trade_id="T2", version=1, maturity_date=date.today() + timedelta(days=20)
        )
        trade_v1_updated.counterparty_id = "CP-99"
        await repo.upsert(trade_v1_updated)
        await session.commit()

        result = await repo.get_by_id_and_version("T2", 1)
        assert result.counterparty_id == "CP-99"

    async def test_get_max_version_returns_none_for_unknown_trade(self, session):
        repo = TradeRepository(session)
        result = await repo.get_max_version("UNKNOWN")
        assert result is None

    async def test_get_max_version_returns_highest_version(self, session):
        repo = TradeRepository(session)
        for v in [1, 2, 3]:
            await repo.upsert(make_trade_model(trade_id="T3", version=v))
        await session.commit()

        result = await repo.get_max_version("T3")
        assert result == 3

    async def test_get_all_returns_all_trades(self, session):
        repo = TradeRepository(session)
        await repo.upsert(make_trade_model(trade_id="T1", version=1))
        await repo.upsert(make_trade_model(trade_id="T2", version=1))
        await repo.upsert(make_trade_model(trade_id="T2", version=2))
        await session.commit()

        all_trades = await repo.get_all()
        assert len(all_trades) == 3

    async def test_get_all_versions_for_trade(self, session):
        repo = TradeRepository(session)
        for v in [1, 2]:
            await repo.upsert(make_trade_model(trade_id="T2", version=v))
        await session.commit()

        versions = await repo.get_all_versions("T2")
        assert len(versions) == 2
        assert {t.version for t in versions} == {1, 2}

    async def test_expired_is_computed_correctly(self, session):
        repo = TradeRepository(session)
        past_trade = make_trade_model(
            trade_id="T_PAST", version=1, maturity_date=date(2014, 5, 20)
        )
        future_trade = make_trade_model(
            trade_id="T_FUTURE",
            version=1,
            maturity_date=date.today() + timedelta(days=30),
        )
        await repo.upsert(past_trade)
        await repo.upsert(future_trade)
        await session.commit()

        past = await repo.get_by_id_and_version("T_PAST", 1)
        future = await repo.get_by_id_and_version("T_FUTURE", 1)
        assert past.expired is True
        assert future.expired is False
