from enum import Enum
from typing import Optional

from app.exceptions.trade_exceptions import LowerVersionError
from app.schemas.trade import TradeCreate
from app.validators.base import TradeValidator


class TradeAction(str, Enum):
    INSERT = "insert"
    UPSERT = "upsert"


class VersionValidator(TradeValidator):
    def __init__(self, current_max_version: Optional[int]) -> None:
        self.current_max_version = current_max_version

    def validate(self, trade: TradeCreate) -> TradeAction:
        if self.current_max_version is None:
            return TradeAction.INSERT
        if trade.version < self.current_max_version:
            raise LowerVersionError(
                f"Trade {trade.trade_id} rejected: incoming version {trade.version} "
                f"is lower than stored version {self.current_max_version}."
            )
        if trade.version == self.current_max_version:
            return TradeAction.UPSERT
        return TradeAction.INSERT
