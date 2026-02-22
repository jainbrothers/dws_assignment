from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    trade_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    counterparty_id: Mapped[str] = mapped_column(String(50), nullable=False)
    book_id: Mapped[str] = mapped_column(String(50), nullable=False)
    maturity_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def expired(self) -> bool:
        """Derived field — computed at access time from maturity_date vs today."""
        return self.maturity_date < date.today()

    def __repr__(self) -> str:
        return f"<Trade {self.trade_id} v{self.version}>"
