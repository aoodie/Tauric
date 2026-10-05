"""Permit encrypted macro-provider revisions and quote-currency paper accounts.

+No historical rows, account policies, deployments or execution controls change.
"""

from alembic import op

revision = "0021_forex_research_paper"
down_revision = "0020_market_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE market_connection_versions DROP CONSTRAINT market_connection_versions_provider_check"
    )
    op.execute(
        "ALTER TABLE market_connection_versions ADD CONSTRAINT market_connection_versions_provider_check CHECK (provider IN ('oanda','alpaca','trading_economics'))"
    )
    op.execute("ALTER TABLE paper_accounts DROP CONSTRAINT paper_accounts_currency_check")
    op.execute(
        "ALTER TABLE paper_accounts ADD CONSTRAINT paper_accounts_currency_check CHECK (currency IN ('USDT','USD','JPY'))"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; financial and credential history is immutable.")
