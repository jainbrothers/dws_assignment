from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import Trade


class TradeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, trade: Trade) -> None:
        """Insert or update a trade record (matched on trade_id + version)."""
        existing = await self.get_by_id_and_version(trade.trade_id, trade.version)
        if existing:
            existing.counterparty_id = trade.counterparty_id
            existing.book_id = trade.book_id
            existing.maturity_date = trade.maturity_date
            existing.created_date = trade.created_date
        else:
            self._session.add(trade)

    async def get_by_id_and_version(
        self, trade_id: str, version: int
    ) -> Optional[Trade]:
        result = await self._session.execute(
            select(Trade).where(Trade.trade_id == trade_id, Trade.version == version)
        )
        return result.scalar_one_or_none()

    async def get_max_version(self, trade_id: str) -> Optional[int]:
        result = await self._session.execute(
            select(func.max(Trade.version)).where(Trade.trade_id == trade_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> List[Trade]:
        result = await self._session.execute(
            select(Trade).order_by(Trade.trade_id, Trade.version)
        )
        return list(result.scalars().all())

    async def get_all_versions(self, trade_id: str) -> List[Trade]:
        result = await self._session.execute(
            select(Trade)
            .where(Trade.trade_id == trade_id)
            .order_by(Trade.version)
        )
        return list(result.scalars().all())
