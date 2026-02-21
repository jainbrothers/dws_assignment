from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradeIngestStatus(str, Enum):
    ACCEPTED = "accepted"
    TEMPORARY_FAILURE = "temporary_failure"


class TradeCreate(BaseModel):
    trade_id: str = Field(..., max_length=20, examples=["T1"])
    version: int = Field(..., ge=1, examples=[1])
    counterparty_id: str = Field(..., max_length=50, examples=["CP-1"])
    book_id: str = Field(..., max_length=50, examples=["B1"])
    maturity_date: date = Field(..., examples=["2025-05-20"])
    created_date: date = Field(..., examples=["2024-01-15"])

    @field_validator("trade_id")
    @classmethod
    def trade_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("trade_id must not be empty")
        return v.strip()


class TradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trade_id: str
    version: int
    counterparty_id: str
    book_id: str
    maturity_date: date
    created_date: date
    expired: bool
    created_at: datetime
    updated_at: datetime


class TradeAccepted(BaseModel):
    status: TradeIngestStatus = TradeIngestStatus.ACCEPTED
    request_id: str
    trade_id: str
    version: int
    message: str = "Trade queued for processing"


class TradeTemporaryFailure(BaseModel):
    status: TradeIngestStatus = TradeIngestStatus.TEMPORARY_FAILURE
    message: str = "Service temporarily unavailable. Please retry later."
    retry_after_seconds: int | None = 60


class TradeKafkaMessage(BaseModel):

    request_id: str
    trade_id: str
    version: int
    counterparty_id: str
    book_id: str
    maturity_date: str
    created_date: str
    action: str
