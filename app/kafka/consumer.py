import asyncio
import json
import signal
from datetime import date
from typing import Optional

import aioboto3
from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.database import AsyncSessionLocal
from app.exceptions.trade_exceptions import TradeValidationError
from app.kafka.topics import TRADES_INBOUND
from app.logging_config import get_logger
from app.models.trade import Trade
from app.repositories.request_repository import RequestRepository
from app.repositories.trade_repository import TradeRepository
from app.schemas.trade import TradeCreate
from app.validators.maturity_date_validator import MaturityDateValidator
from app.validators.version_validator import VersionValidator

logger = get_logger(__name__)

CONSUMER_GROUP_ID = "trade-store-consumer"


async def handle_message(
    message_value: dict,
    session_factory=None,
    dynamodb_client=None,
) -> None:
    if session_factory is None:
        session_factory = AsyncSessionLocal

    request_id: Optional[str] = message_value.get("request_id")
    request_repo = (
        RequestRepository(dynamodb_client, settings.dynamodb_table_name)
        if dynamodb_client
        else None
    )

    async with session_factory() as session:
        repo = TradeRepository(session)

        trade_create = TradeCreate(
            trade_id=message_value["trade_id"],
            version=message_value["version"],
            counterparty_id=message_value["counterparty_id"],
            book_id=message_value["book_id"],
            maturity_date=date.fromisoformat(message_value["maturity_date"]),
            created_date=date.fromisoformat(message_value["created_date"]),
        )

        try:
            MaturityDateValidator().validate(trade_create)
            current_max = await repo.get_max_version(trade_create.trade_id)
            action = VersionValidator(current_max_version=current_max).validate(trade_create)
        except TradeValidationError as exc:
            logger.warning(
                "trade_validation_failed",
                trade_id=trade_create.trade_id,
                version=trade_create.version,
                reason=exc.message,
                request_id=request_id,
            )
            if request_repo and request_id:
                await request_repo.update_status(request_id, "FAILED", exc.message)
            return

        trade = Trade(
            trade_id=trade_create.trade_id,
            version=trade_create.version,
            counterparty_id=trade_create.counterparty_id,
            book_id=trade_create.book_id,
            maturity_date=trade_create.maturity_date,
            created_date=trade_create.created_date,
        )
        await repo.upsert(trade)
        await session.commit()

        if request_repo and request_id:
            await request_repo.update_status(request_id, "SUCCESS")

        logger.info(
            "trade_persisted",
            trade_id=trade.trade_id,
            version=trade.version,
            action=action,
            request_id=request_id,
        )


async def consume() -> None:
    session = aioboto3.Session()
    async with session.client(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint_url or None,
    ) as dynamodb_client:
        consumer = AIOKafkaConsumer(
            TRADES_INBOUND,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=CONSUMER_GROUP_ID,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=True,
        )
        await consumer.start()
        logger.info("kafka_consumer_started", topic=TRADES_INBOUND)

        try:
            async for msg in consumer:
                try:
                    await handle_message(msg.value, dynamodb_client=dynamodb_client)
                except Exception as exc:
                    logger.error(
                        "trade_persist_failed",
                        error=str(exc),
                        payload=msg.value,
                    )
        finally:
            await consumer.stop()
            logger.info("kafka_consumer_stopped")


def main() -> None:
    loop = asyncio.get_event_loop()

    def shutdown():
        logger.info("consumer_shutdown_signal_received")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    loop.run_until_complete(consume())


if __name__ == "__main__":
    main()
