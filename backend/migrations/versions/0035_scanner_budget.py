"""Durable read-only provider budgets across processes and restarts."""

from alembic import op

revision = "0035_scanner_budget"
down_revision = "0034_quant_lab"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE assistant_records ALTER COLUMN operation TYPE VARCHAR(64);
      CREATE TABLE market_request_budgets (
        provider VARCHAR(24) PRIMARY KEY CHECK(provider IN ('oanda_v20','alpaca_stocks')),
        tokens NUMERIC(18,6) NOT NULL CHECK(tokens >= 0),
        updated_at TIMESTAMPTZ NOT NULL, retry_at TIMESTAMPTZ
      );
      GRANT SELECT,INSERT,UPDATE ON market_request_budgets TO tauric_app;
      CREATE INDEX activity_research_history ON analysis_jobs(owner_id,created_at DESC,id DESC);
      CREATE INDEX activity_governance_history ON governance_jobs(owner_id,created_at DESC,id DESC);
      CREATE INDEX activity_chat_history ON chat_turns(owner_id,created_at DESC,id DESC);
      CREATE INDEX activity_scan_history ON scanner_runs(owner_id,created_at DESC,id DESC);
      CREATE INDEX activity_candidate_history ON scanner_candidates(owner_id,created_at DESC,id DESC);
      CREATE INDEX activity_quant_history ON quant_records(owner_id,created_at DESC,id DESC);
      CREATE INDEX activity_audit_history ON audit_events(owner_id,created_at DESC,id DESC);
      CREATE INDEX scanner_profile_progress ON scanner_runs(profile_version_id,created_at DESC,id DESC);
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup before changing the running release schema.")
