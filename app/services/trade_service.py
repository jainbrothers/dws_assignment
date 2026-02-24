import uuid
from typing import Callable, List, Union

from app.kafka.topics import TRADES_INBOUND
from app.logging_config import get_logger
from app.models.trade import Trade
from app.repositories.request_repository import RequestRepository
from app.repositories.trade_repository import TradeRepository
from app.schemas.trade import TradeAccepted, TradeCreate, TradeTemporaryFailure

logger = get_logger()


class TradeService:
    def __init__(
        self,
        repo: TradeRepository,
        producer: Callable,
        request_repo: RequestRepository,
    ) -> None:
        self._repo = repo
        self._producer = producer
        self._request_repo = request_repo

    async def ingest_trade(
        self, trade: TradeCreate
    ) -> Union[TradeAccepted, TradeTemporaryFailure]:
        logger.info(
            "ingest_trade_received",
            trade_id=trade.trade_id,
            version=trade.version,
        )
        request_id = str(uuid.uuid4())
        payload = {
            "request_id": request_id,
            "trade_id": trade.trade_id,
            "version": trade.version,
            "counterparty_id": trade.counterparty_id,
            "book_id": trade.book_id,
            "maturity_date": trade.maturity_date.isoformat(),
            "created_date": trade.created_date.isoformat(),
            "action": "insert",
        }

        try:
            await self._request_repo.create_pending(request_id, trade)
        except Exception as e:
            logger.warning("create_pending_failed", error=str(e), request_id=request_id)
            return TradeTemporaryFailure(
                message="Service temporarily unavailable. Please retry later.",
                retry_after_seconds=60,
            )

        try:
            await self._producer(
                topic=TRADES_INBOUND, trade_id=trade.trade_id, payload=payload
            )
        except Exception as e:
            logger.warning(
                "kafka_publish_failed",
                error=str(e),
                request_id=request_id,
                trade_id=trade.trade_id,
            )
            return TradeTemporaryFailure(
                message="Service temporarily unavailable. Please retry later.",
                retry_after_seconds=60,
            )

        logger.info(
            "trade_accepted",
            trade_id=trade.trade_id,
            version=trade.version,
            request_id=request_id,
        )
        return TradeAccepted(
            request_id=request_id,
            trade_id=trade.trade_id,
            version=trade.version,
        )

    async def get_all_trades(self) -> List[Trade]:
        return await self._repo.get_all()

    async def get_trade_versions(self, trade_id: str) -> List[Trade]:
        return await self._repo.get_all_versions(trade_id)
