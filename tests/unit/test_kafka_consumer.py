from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.kafka.consumer import handle_message


def make_payload(
    trade_id: str = "T1",
    version: int = 1,
    action: str = "insert",
    maturity_date: date = None,
    request_id: str = "req-test-123", #TODO: Ajit, make it random number
) -> dict:
    md = maturity_date or date.today() + timedelta(days=30)
    return {
        "request_id": request_id,
        "trade_id": trade_id,
        "version": version,
        "counterparty_id": "CP-1",
        "book_id": "B1",
        "maturity_date": md.isoformat(),
        "created_date": date.today().isoformat(),
        "action": action,
    }


def make_session_factory(mock_repo):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_session), mock_session


class TestHandleMessage:
    async def test_valid_trade_upserts_and_marks_success(self):
        mock_repo = AsyncMock()
        mock_repo.get_max_version = AsyncMock(return_value=None)
        mock_session_factory, mock_session = make_session_factory(mock_repo)
        mock_ddb = AsyncMock()

        with patch("app.kafka.consumer.TradeRepository", return_value=mock_repo):
            await handle_message(
                make_payload(),
                session_factory=mock_session_factory,
                dynamodb_client=mock_ddb,
            )

        mock_repo.upsert.assert_awaited_once()
        mock_session.commit.assert_awaited_once()
        mock_ddb.update_item.assert_awaited_once()

        update_call = mock_ddb.update_item.call_args[1]

        assert update_call["ExpressionAttributeValues"][":status"] == {"S": "SUCCESS"}

    async def test_expired_maturity_marks_failed_and_skips_upsert(self):
        mock_repo = AsyncMock()
        mock_repo.get_max_version = AsyncMock(return_value=None)
        mock_session_factory, _ = make_session_factory(mock_repo)
        mock_ddb = AsyncMock()

        with patch("app.kafka.consumer.TradeRepository", return_value=mock_repo):
            await handle_message(
                make_payload(maturity_date=date.today() - timedelta(days=1)),
                session_factory=mock_session_factory,
                dynamodb_client=mock_ddb,
            )

        mock_repo.upsert.assert_not_awaited()
        mock_ddb.update_item.assert_awaited_once()

        update_call = mock_ddb.update_item.call_args[1]

        assert update_call["ExpressionAttributeValues"][":status"] == {"S": "FAILED"}

    async def test_lower_version_marks_failed_and_skips_upsert(self):
        mock_repo = AsyncMock()
        mock_repo.get_max_version = AsyncMock(return_value=5)
        mock_session_factory, _ = make_session_factory(mock_repo)
        mock_ddb = AsyncMock()

        with patch("app.kafka.consumer.TradeRepository", return_value=mock_repo):
            await handle_message(
                make_payload(version=2),
                session_factory=mock_session_factory,
                dynamodb_client=mock_ddb,
            )

        mock_repo.upsert.assert_not_awaited()
        mock_ddb.update_item.assert_awaited_once()

        update_call = mock_ddb.update_item.call_args[1]
        
        assert update_call["ExpressionAttributeValues"][":status"] == {"S": "FAILED"}

    async def test_same_version_upserts_successfully(self):
        mock_repo = AsyncMock()
        mock_repo.get_max_version = AsyncMock(return_value=2)
        mock_session_factory, _ = make_session_factory(mock_repo)
        mock_ddb = AsyncMock()

        with patch("app.kafka.consumer.TradeRepository", return_value=mock_repo):
            await handle_message(
                make_payload(version=2),
                session_factory=mock_session_factory,
                dynamodb_client=mock_ddb,
            )

        mock_repo.upsert.assert_awaited_once()

    async def test_no_dynamodb_client_still_persists(self):
        """Consumer continues to work even without a DynamoDB client (graceful degradation)."""
        mock_repo = AsyncMock()
        mock_repo.get_max_version = AsyncMock(return_value=None)
        mock_session_factory, _ = make_session_factory(mock_repo)

        with patch("app.kafka.consumer.TradeRepository", return_value=mock_repo):
            await handle_message(
                make_payload(),
                session_factory=mock_session_factory,
                dynamodb_client=None,
            )

        mock_repo.upsert.assert_awaited_once()

    async def test_persisted_trade_has_correct_fields(self):
        captured = {}
        mock_repo = AsyncMock()
        mock_repo.get_max_version = AsyncMock(return_value=None)

        async def capture_upsert(trade):
            captured["trade"] = trade

        mock_repo.upsert.side_effect = capture_upsert
        mock_session_factory, _ = make_session_factory(mock_repo)

        with patch("app.kafka.consumer.TradeRepository", return_value=mock_repo):
            await handle_message(
                make_payload(trade_id="T2", version=3),
                session_factory=mock_session_factory,
                dynamodb_client=AsyncMock(),
            )

        trade = captured["trade"]
        assert trade.trade_id == "T2"
        assert trade.version == 3
        assert trade.counterparty_id == "CP-1"
