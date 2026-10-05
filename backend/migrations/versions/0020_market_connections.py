"""Encrypted immutable market-data connection revisions."""

from alembic import op

revision = "0020_market_connections"
down_revision = "0019_market_adapters"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE market_connection_versions (
          id VARCHAR(36) PRIMARY KEY,
          owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
          provider VARCHAR(20) NOT NULL CHECK (provider IN ('oanda','alpaca')),
          revision INTEGER NOT NULL CHECK (revision > 0),
          encrypted_config TEXT NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE NOT NULL,
          UNIQUE (provider, revision)
        )
    """)
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON market_connection_versions FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
    )
    op.execute(
        "CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON market_connection_versions FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
    )
    op.execute("GRANT SELECT, INSERT ON market_connection_versions TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; connection history is immutable.")
