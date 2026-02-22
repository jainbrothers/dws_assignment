from enum import Enum
from typing import Optional

from pydantic import BaseModel


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class RequestStatusResponse(BaseModel):
    request_id: str
    status: RequestStatus
    trade_id: str
    version: int
    failure_reason: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
