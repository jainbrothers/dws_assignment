import json
import ssl
from typing import Callable, Optional

import certifi
from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from app.config import settings
from app.kafka.topics import TRADES_INBOUND
from app.logging_config import get_logger

logger = get_logger()

_producer: Optional[AIOKafkaProducer] = None


async def _ensure_topics_exist(ssl_context) -> None:
    """Create required Kafka topics if they don't exist yet (idempotent)."""
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        security_protocol=settings.kafka_security_protocol,
        ssl_context=ssl_context,
    )
    await admin.start()
    try:
        await admin.create_topics(
            [
                NewTopic(
                    name=TRADES_INBOUND,
                    num_partitions=3,
                    replication_factor=settings.kafka_topic_replication_factor,
                )
            ]
        )
        logger.info("kafka_topic_created", topic=TRADES_INBOUND)
    except TopicAlreadyExistsError:
        logger.info("kafka_topic_exists", topic=TRADES_INBOUND)
    finally:
        await admin.close()


async def start_producer() -> None:
    global _producer
    ssl_context = None
    if settings.kafka_security_protocol == "SSL":
        ssl_context = ssl.create_default_context(cafile=certifi.where())

    await _ensure_topics_exist(ssl_context)

    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        security_protocol=settings.kafka_security_protocol,
        ssl_context=ssl_context,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await producer.start()
    _producer = producer
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
