from abc import ABC, abstractmethod

from app.schemas.trade import TradeCreate


class TradeValidator(ABC):
    @abstractmethod
    def validate(self, trade: TradeCreate) -> None:
        ...