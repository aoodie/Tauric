"""Encrypted TradingView OAuth connection and short-lived sign-in handshakes."""

from alembic import op

revision = "0022_tradingview_mcp"
down_revision = "0021_forex_research_paper"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE market_connection_versions DROP CONSTRAINT market_connection_versions_provider_check"
    )
    op.execute(
        "ALTER TABLE market_connection_versions ADD CONSTRAINT market_connection_versions_provider_check CHECK (provider IN ('oanda','alpaca','trading_economics','tradingview'))"
    )
    op.execute("""CREATE TABLE tradingview_oauth_flows (
      state_hash VARCHAR(64) PRIMARY KEY,
      owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      session_hash VARCHAR(64) NOT NULL REFERENCES owner_sessions(token_hash),
      expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
      consumed BOOLEAN NOT NULL DEFAULT false,
      encrypted_config TEXT NOT NULL
    )""")
    op.execute("GRANT SELECT, INSERT, UPDATE ON tradingview_oauth_flows TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; credential history is immutable.")
