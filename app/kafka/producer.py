import json
from typing import Callable, Optional

from aiokafka import AIOKafkaProducer

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_producer: Optional[AIOKafkaProducer] = None


async def start_producer() -> None:
    global _producer
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await _producer.start()
    logger.info("kafka_producer_started", servers=settings.kafka_bootstrap_servers)


async def stop_producer() -> None:
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
        logger.info("kafka_producer_stopped")


async def send_trade(topic: str, trade_id: str, payload: dict) -> None:
    if _producer is None:
        raise RuntimeError(
            "Kafka producer not initialised. Call start_producer() first."
        )
    await _producer.send_and_wait(topic, key=trade_id, value=payload)
    logger.info("kafka_message_sent", topic=topic, trade_id=trade_id)


def get_producer() -> Optional[AIOKafkaProducer]:
    return _producer


def get_kafka_send() -> Callable:
    """FastAPI dependency that returns the send_trade callable for injection."""
    return send_trade
