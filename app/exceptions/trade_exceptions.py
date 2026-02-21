class TradeValidationError(Exception):
    """Base class for trade validation errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class MaturityDateExpiredError(TradeValidationError):
    """Raised when a trade's maturity date is in the past."""


class LowerVersionError(TradeValidationError):
    """Raised when an incoming trade version is lower than the current stored version."""
