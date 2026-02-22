"""
Integration tests: in-memory SQLite + mocked Kafka + mocked DynamoDB.
"""

import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from tests.conftest import trade_payload


class TestPostTrade:
    async def test_valid_trade_returns_202_with_request_id(self, client):
        resp = await client.post("/api/v1/trades", json=trade_payload())
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["trade_id"] == "T1"
        assert body["version"] == 1
        assert body["request_id"]  # non-empty UUID
        uuid.UUID(body["request_id"])  # valid UUID format

    async def test_past_maturity_date_accepted_at_api(self, client):
        """Validation is consumer-side; API accepts any schema-valid trade."""
        resp = await client.post(
            "/api/v1/trades",
            json=trade_payload(maturity_date=date.today() - timedelta(days=1)),
        )
        assert resp.status_code == 202

    async def test_lower_version_accepted_at_api(self, client, db_session):
        """Version check is consumer-side; API no longer rejects lower versions."""
        from app.models.trade import Trade
        from app.repositories.trade_repository import TradeRepository

        repo = TradeRepository(db_session)
        await repo.upsert(
            Trade(
                trade_id="T2",
                version=2,
                counterparty_id="CP-1",
                book_id="B1",
                maturity_date=date.today() + timedelta(days=30),
                created_date=date.today(),
            )
        )
        await db_session.commit()

        resp = await client.post(
            "/api/v1/trades", json=trade_payload(trade_id="T2", version=1)
        )
        assert resp.status_code == 202

    async def test_invalid_payload_returns_422(self, client):
        resp = await client.post("/api/v1/trades", json={"trade_id": "", "version": -1})
        assert resp.status_code == 422

    async def test_ddb_put_called_on_ingest(self, client, mock_dynamodb_client):
        resp = await client.post("/api/v1/trades", json=trade_payload())
        assert resp.status_code == 202
        mock_dynamodb_client.put_item.assert_awaited_once()

    async def test_kafka_not_published_if_ddb_fails(
        self, client, mock_dynamodb_client, mock_kafka_send
    ):
        mock_dynamodb_client.put_item.side_effect = RuntimeError("DynamoDB unavailable")
        resp = await client.post("/api/v1/trades", json=trade_payload())
        assert resp.status_code == 503
        assert resp.json()["status"] == "temporary_failure"
        mock_kafka_send.assert_not_awaited()


class TestGetRequestStatus:
    async def test_pending_request_returns_200(self, client, mock_dynamodb_client):
        request_id = str(uuid.uuid4())
        mock_dynamodb_client.get_item.return_value = {
            "Item": {
                "PK": {"S": request_id},
                "request_id": {"S": request_id},
                "status": {"S": "PENDING"},
                "trade_id": {"S": "T1"},
                "version": {"N": "1"},
                "created_at": {"S": "2026-02-22T10:00:00+00:00"},
            }
        }
        resp = await client.get(f"/api/v1/requests/{request_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == request_id
        assert body["status"] == "PENDING"
        assert body["trade_id"] == "T1"
        assert body["failure_reason"] is None

    async def test_success_request_returns_200(self, client, mock_dynamodb_client):
        request_id = str(uuid.uuid4())
        mock_dynamodb_client.get_item.return_value = {
            "Item": {
                "PK": {"S": request_id},
                "request_id": {"S": request_id},
                "status": {"S": "SUCCESS"},
                "trade_id": {"S": "T1"},
                "version": {"N": "2"},
                "created_at": {"S": "2026-02-22T10:00:00+00:00"},
                "updated_at": {"S": "2026-02-22T10:00:01+00:00"},
            }
        }
        resp = await client.get(f"/api/v1/requests/{request_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "SUCCESS"

    async def test_failed_request_includes_reason(self, client, mock_dynamodb_client):
        request_id = str(uuid.uuid4())
        mock_dynamodb_client.get_item.return_value = {
            "Item": {
                "PK": {"S": request_id},
                "request_id": {"S": request_id},
                "status": {"S": "FAILED"},
                "trade_id": {"S": "T1"},
                "version": {"N": "1"},
                "failure_reason": {"S": "Maturity date is in the past"},
                "created_at": {"S": "2026-02-22T10:00:00+00:00"},
                "updated_at": {"S": "2026-02-22T10:00:01+00:00"},
            }
        }
        resp = await client.get(f"/api/v1/requests/{request_id}")
        assert resp.status_code == 200
        assert resp.json()["failure_reason"] == "Maturity date is in the past"

    async def test_unknown_request_id_returns_404(self, client, mock_dynamodb_client):
        mock_dynamodb_client.get_item.return_value = {}
        resp = await client.get("/api/v1/requests/nonexistent-id")
        assert resp.status_code == 404


class TestGetTrades:
    async def test_get_all_trades_returns_200(self, client):
        resp = await client.get("/api/v1/trades")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_trade_by_id_returns_all_versions(self, client, db_session):
        from app.models.trade import Trade
        from app.repositories.trade_repository import TradeRepository

        repo = TradeRepository(db_session)
        for v in [1, 2]:
            await repo.upsert(
                Trade(
                    trade_id="T5",
                    version=v,
                    counterparty_id="CP-1",
                    book_id="B1",
                    maturity_date=date.today() + timedelta(days=30),
                    created_date=date.today(),
                )
            )
        await db_session.commit()

        resp = await client.get("/api/v1/trades/T5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert {t["version"] for t in data} == {1, 2}

    async def test_expired_field_computed_correctly(self, client, db_session):
        from app.models.trade import Trade
        from app.repositories.trade_repository import TradeRepository

        repo = TradeRepository(db_session)
        await repo.upsert(
            Trade(
                trade_id="T_OLD",
                version=1,
                counterparty_id="CP-1",
                book_id="B1",
                maturity_date=date(2014, 5, 20),
                created_date=date.today(),
            )
        )
        await db_session.commit()

        resp = await client.get("/api/v1/trades/T_OLD")
        assert resp.status_code == 200
        assert resp.json()[0]["expired"] is True


class TestHealthEndpoint:
    @patch(
        "app.routers.health.kafka_producer_module.get_producer",
        return_value=MagicMock(),
    )
    async def test_health_returns_200_ok_when_db_and_kafka_ok(
        self, _mock_get_producer, client
    ):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["db"] == "ok"
        assert resp.json()["kafka"] == "ok"

    async def test_health_returns_degraded_when_kafka_not_initialised(self, client):
        with patch(
            "app.routers.health.kafka_producer_module.get_producer", return_value=None
        ):
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"
        assert resp.json()["kafka"] == "not_initialised"
