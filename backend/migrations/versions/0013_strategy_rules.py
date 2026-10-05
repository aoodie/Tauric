"""Immutable paper strategy evaluations and single-use entry signals."""

from alembic import op

revision = "0013_strategy_rules"
down_revision = "0012_research_analytics"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE strategy_runs (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id),
        strategy_version_id VARCHAR(36) NOT NULL REFERENCES strategy_versions(id),
        snapshot_id VARCHAR(36) REFERENCES market_snapshots(id), request_id VARCHAR(36) NOT NULL,
        action VARCHAR(12) NOT NULL CHECK(action IN ('enter','exit','hold','blocked')),
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(owner_id,request_id)
      );
      CREATE INDEX strategy_runs_deployment ON strategy_runs(deployment_id,created_at);
      CREATE UNIQUE INDEX strategy_entry_once ON trading_records(deployment_id,(payload->>'strategy_signal_key'))
        WHERE kind='proposal' AND payload->>'strategy_signal_key' IS NOT NULL;
      CREATE FUNCTION guard_strategy_run() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM paper_deployments d JOIN strategy_versions v ON v.id=NEW.strategy_version_id
          WHERE d.id=NEW.deployment_id AND d.owner_id=NEW.owner_id AND v.owner_id=NEW.owner_id
            AND d.payload->>'strategy_revision_id'=v.id)
          THEN RAISE EXCEPTION 'Strategy run must match the owned pinned deployment'; END IF;
        IF NEW.snapshot_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM market_snapshots s
          JOIN paper_deployments d ON d.instrument_id=s.instrument_id
          JOIN strategy_versions v ON v.id=NEW.strategy_version_id
          WHERE d.id=NEW.deployment_id AND s.id=NEW.snapshot_id
            AND s.interval=v.payload->'parameters'->'rules'->>'interval')
          THEN RAISE EXCEPTION 'Strategy snapshot must match instrument and rule interval'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER strategy_run_guard BEFORE INSERT ON strategy_runs FOR EACH ROW EXECUTE FUNCTION guard_strategy_run();
      CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON strategy_runs FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
      CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON strategy_runs FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
      GRANT SELECT,INSERT ON strategy_runs TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; strategy run history is immutable.")
