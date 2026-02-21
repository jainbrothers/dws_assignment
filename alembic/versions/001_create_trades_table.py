"""create trades table

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trades",
        sa.Column("trade_id", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("counterparty_id", sa.String(50), nullable=False),
        sa.Column("book_id", sa.String(50), nullable=False),
        sa.Column("maturity_date", sa.Date, nullable=False),
        sa.Column("created_date", sa.Date, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("trade_id", "version"),
    )
    op.create_index("idx_trades_maturity_date", "trades", ["maturity_date"])
    op.create_index("idx_trades_trade_id", "trades", ["trade_id"])


def downgrade() -> None:
    op.drop_index("idx_trades_trade_id", table_name="trades")
    op.drop_index("idx_trades_maturity_date", table_name="trades")
    op.drop_table("trades")
