import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

from app.schemas.trade import TradeAccepted, TradeCreate
from app.services.trade_service import TradeService


def make_trade(
    trade_id: str = "T1",
    version: int = 1,
    maturity_date: date = None,
) -> TradeCreate:
    return TradeCreate(
        trade_id=trade_id,
        version=version,
        counterparty_id="CP-1",
        book_id="B1",
        maturity_date=maturity_date or date.today() + timedelta(days=30),
        created_date=date.today(),
    )


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_all = AsyncMock(return_value=[])
    repo.get_all_versions = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_producer():
    return AsyncMock()


@pytest.fixture
def mock_request_repo():
    repo = AsyncMock()
    repo.create_pending = AsyncMock(return_value=None)
    return repo


class TestTradeService:
    async def test_valid_trade_creates_pending_and_publishes(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        result = await service.ingest_trade(make_trade(version=1))

        assert isinstance(result, TradeAccepted)
        assert result.status == "accepted"
        assert result.trade_id == "T1"
        assert result.version == 1
        mock_request_repo.create_pending.assert_awaited_once()
        mock_producer.assert_awaited_once()

    async def test_returned_request_id_is_uuid(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        result = await service.ingest_trade(make_trade())
        uuid.UUID(result.request_id) 

    async def test_kafka_payload_includes_request_id_and_trade_fields(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        await service.ingest_trade(make_trade(trade_id="T2", version=3))

        payload = mock_producer.call_args[1]["payload"]
        assert "request_id" in payload
        assert payload["trade_id"] == "T2"
        assert payload["version"] == 3
        assert payload["action"] == "insert"

    async def test_past_maturity_date_accepted_at_api(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        """Validation is consumer-side; API accepts any schema-valid trade."""
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        result = await service.ingest_trade(
            make_trade(maturity_date=date.today() - timedelta(days=1))
        )
        assert result.status == "accepted"
        mock_producer.assert_awaited_once()

    async def test_lower_version_accepted_at_api(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        """Version check is consumer-side; API accepts any schema-valid trade."""
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        result = await service.ingest_trade(make_trade(version=1))
        assert result.status == "accepted"
        mock_producer.assert_awaited_once()

    async def test_ddb_failure_rolls_back_before_kafka(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        """If DynamoDB write fails, Kafka must NOT be published."""
        mock_request_repo.create_pending.side_effect = RuntimeError("DynamoDB unavailable")
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        with pytest.raises(RuntimeError, match="DynamoDB unavailable"):
            await service.ingest_trade(make_trade())
        mock_producer.assert_not_awaited()

    async def test_get_all_trades_delegates_to_repo(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        result = await service.get_all_trades()
        mock_repo.get_all.assert_awaited_once()
        assert result == []

    async def test_get_trade_versions_delegates_to_repo(
        self, mock_repo, mock_producer, mock_request_repo
    ):
        service = TradeService(
            repo=mock_repo, producer=mock_producer, request_repo=mock_request_repo
        )
        result = await service.get_trade_versions("T1")
        mock_repo.get_all_versions.assert_awaited_once_with("T1")
        assert result == []
