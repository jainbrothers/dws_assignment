from datetime import date

from app.exceptions.trade_exceptions import MaturityDateExpiredError
from app.schemas.trade import TradeCreate
from app.validators.base import TradeValidator


class MaturityDateValidator(TradeValidator):
    def validate(self, trade: TradeCreate) -> None:
        if trade.maturity_date < date.today():
            raise MaturityDateExpiredError(
                f"Trade {trade.trade_id} rejected: maturity date {trade.maturity_date} "
                f"is earlier than today ({date.today()})."
            )
