from datetime import date, timedelta

import pytest

from app.exceptions.trade_exceptions import LowerVersionError, MaturityDateExpiredError
from app.schemas.trade import TradeCreate
from app.validators.maturity_date_validator import MaturityDateValidator
from app.validators.version_validator import VersionValidator


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


class TestMaturityDateValidator:
    def test_future_maturity_date_passes(self):
        trade = make_trade(maturity_date=date.today() + timedelta(days=1))
        validator = MaturityDateValidator()
        # Should not raise
        validator.validate(trade)

    def test_today_maturity_date_passes(self):
        trade = make_trade(maturity_date=date.today())
        validator = MaturityDateValidator()
        validator.validate(trade)

    def test_past_maturity_date_raises(self):
        trade = make_trade(maturity_date=date.today() - timedelta(days=1))
        validator = MaturityDateValidator()
        with pytest.raises(MaturityDateExpiredError) as exc_info:
            validator.validate(trade)
        assert "maturity" in exc_info.value.message.lower()

    def test_far_past_maturity_date_raises(self):
        trade = make_trade(maturity_date=date(2014, 5, 20))
        validator = MaturityDateValidator()
        with pytest.raises(MaturityDateExpiredError):
            validator.validate(trade)


class TestVersionValidator:
    def test_higher_version_passes(self):
        trade = make_trade(version=3)
        validator = VersionValidator(current_max_version=2)
        action = validator.validate(trade)
        assert action == "insert"

    def test_same_version_passes_as_upsert(self):
        trade = make_trade(version=2)
        validator = VersionValidator(current_max_version=2)
        action = validator.validate(trade)
        assert action == "upsert"

    def test_lower_version_raises(self):
        trade = make_trade(version=1)
        validator = VersionValidator(current_max_version=2)
        with pytest.raises(LowerVersionError) as exc_info:
            validator.validate(trade)
        assert "version" in exc_info.value.message.lower()

    def test_version_one_with_no_existing_trade_passes(self):
        trade = make_trade(version=1)
        validator = VersionValidator(current_max_version=None)
        action = validator.validate(trade)
        assert action == "insert"

    def test_version_lower_than_zero_same_logic(self):
        """Any incoming version < stored max is rejected."""
        trade = make_trade(version=1)
        validator = VersionValidator(current_max_version=5)
        with pytest.raises(LowerVersionError):
            validator.validate(trade)
