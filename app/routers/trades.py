from typing import Callable, List

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dynamodb import get_dynamodb_client
from app.kafka.producer import get_kafka_send
from app.repositories.request_repository import RequestRepository
from app.repositories.trade_repository import TradeRepository
from app.schemas.trade import (
    TradeAccepted,
    TradeCreate,
    TradeResponse,
    TradeTemporaryFailure,
)
from app.services.trade_service import TradeService

router = APIRouter(prefix="/api/v1/trades", tags=["trades"])


def get_service(
    db: AsyncSession = Depends(get_db),
    send_trade: Callable = Depends(get_kafka_send),
    dynamodb_client=Depends(get_dynamodb_client),
) -> TradeService:
    repo = TradeRepository(db)
    request_repo = RequestRepository(dynamodb_client, settings.dynamodb_table_name)
    return TradeService(repo=repo, producer=send_trade, request_repo=request_repo)


@router.post(
    "",
    response_model=TradeAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": TradeTemporaryFailure,
            "description": "DDB or Kafka temporarily unavailable; retry later.",
        },
    },
)
async def ingest_trade(
    trade: TradeCreate,
    service: TradeService = Depends(get_service),
):
    result = await service.ingest_trade(trade)
    if isinstance(result, TradeTemporaryFailure):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=result.model_dump(),
            headers={
                "Retry-After": (
                    str(result.retry_after_seconds)
                    if result.retry_after_seconds
                    else "60"
                ),
            },
        )
    return result


@router.get("", response_model=List[TradeResponse])
async def list_trades(service: TradeService = Depends(get_service)):
    trades = await service.get_all_trades()
    return [_to_response(t) for t in trades]


@router.get("/{trade_id}", response_model=List[TradeResponse])
async def get_trade_versions(
    trade_id: str,
    service: TradeService = Depends(get_service),
):
    trades = await service.get_trade_versions(trade_id)
    return [_to_response(t) for t in trades]


def _to_response(trade) -> TradeResponse:
    return TradeResponse(
        trade_id=trade.trade_id,
        version=trade.version,
        counterparty_id=trade.counterparty_id,
        book_id=trade.book_id,
        maturity_date=trade.maturity_date,
        created_date=trade.created_date,
        expired=trade.expired,
        created_at=trade.created_at,
        updated_at=trade.updated_at,
    )
