import time
from datetime import datetime, timezone
from typing import Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class RequestRepository:
    def __init__(self, dynamodb_client, table_name: str) -> None:
        self._client = dynamodb_client
        self._table_name = table_name

    async def create_pending(self, request_id: str, trade) -> None:
        """Write a new PENDING request record to DynamoDB."""
        await self._client.put_item(
            TableName=self._table_name,
            Item={
                "PK": {"S": request_id},
                "request_id": {"S": request_id},
                "status": {"S": "PENDING"},
                "trade_id": {"S": trade.trade_id},
                "version": {"N": str(trade.version)},
                "counterparty_id": {"S": trade.counterparty_id},
                "book_id": {"S": trade.book_id},
                "maturity_date": {"S": trade.maturity_date.isoformat()},
                "created_date": {"S": trade.created_date.isoformat()},
                "created_at": {"S": datetime.now(timezone.utc).isoformat()},
                "ttl": {"N": str(int(time.time()) + _TTL_SECONDS)},
            },
        )
        logger.info("request_created", request_id=request_id, trade_id=trade.trade_id)

    async def update_status(
        self,
        request_id: str,
        status: str,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Update request lifecycle status; optionally record a failure reason."""
        updated_at = datetime.now(timezone.utc).isoformat()
        if failure_reason:
            await self._client.update_item(
                TableName=self._table_name,
                Key={"PK": {"S": request_id}},
                UpdateExpression="SET #s = :status, updated_at = :ts, failure_reason = :reason",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":status": {"S": status},
                    ":ts": {"S": updated_at},
                    ":reason": {"S": failure_reason},
                },
            )
        else:
            await self._client.update_item(
                TableName=self._table_name,
                Key={"PK": {"S": request_id}},
                UpdateExpression="SET #s = :status, updated_at = :ts",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":status": {"S": status},
                    ":ts": {"S": updated_at},
                },
            )
        logger.info("request_status_updated", request_id=request_id, status=status)

    async def get_by_request_id(self, request_id: str) -> Optional[dict]:
        """Fetch a request record. Returns None when request_id does not exist."""
        response = await self._client.get_item(
            TableName=self._table_name,
            Key={"PK": {"S": request_id}},
        )
        item = response.get("Item")
        if not item:
            return None
        return {
            "request_id": item["request_id"]["S"],
            "status": item["status"]["S"],
            "trade_id": item["trade_id"]["S"],
            "version": int(item["version"]["N"]),
            "failure_reason": item.get("failure_reason", {}).get("S"),
            "created_at": item["created_at"]["S"],
            "updated_at": item.get("updated_at", {}).get("S"),
        }
