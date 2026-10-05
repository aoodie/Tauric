"""Versioned opt-in paper monitors and atomic, immutable scheduled checks."""

from alembic import op

revision = "0014_strategy_schedules"
down_revision = "0013_strategy_rules"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE strategy_schedule_versions (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id),
        revision INTEGER NOT NULL CHECK(revision > 0), request_id VARCHAR(36) NOT NULL,
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(deployment_id,revision), UNIQUE(owner_id,request_id),
        CHECK(json_typeof(payload->'enabled')='boolean'),
        CHECK((payload->>'cadence_seconds')::int BETWEEN 30 AND 3600)
      );
      CREATE TABLE strategy_schedule_ticks (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id),
        version_id VARCHAR(36) NOT NULL REFERENCES strategy_schedule_versions(id),
        due_at TIMESTAMPTZ NOT NULL, run_id VARCHAR(36) REFERENCES strategy_runs(id),
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(version_id,due_at)
      );
      CREATE INDEX schedule_tick_history ON strategy_schedule_ticks(deployment_id,created_at);
      CREATE TABLE strategy_schedules (
        deployment_id VARCHAR(36) PRIMARY KEY REFERENCES paper_deployments(id),
        owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        version_id VARCHAR(36) NOT NULL REFERENCES strategy_schedule_versions(id),
        next_due_at TIMESTAMPTZ, last_tick_id VARCHAR(36) REFERENCES strategy_schedule_ticks(id)
      );
      CREATE INDEX schedule_due ON strategy_schedules(next_due_at) WHERE next_due_at IS NOT NULL;
      CREATE FUNCTION guard_strategy_schedule_version() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE previous_revision INTEGER;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM paper_deployments d JOIN strategy_versions v
          ON v.id=d.payload->>'strategy_revision_id'
          WHERE d.id=NEW.deployment_id AND d.owner_id=NEW.owner_id AND v.owner_id=NEW.owner_id
          AND v.payload->'parameters'->'rules'->>'engine'='closed_bar_breakout_v1')
          THEN RAISE EXCEPTION 'Schedule requires owned executable paper deployment'; END IF;
        SELECT COALESCE(MAX(revision),0) INTO previous_revision FROM strategy_schedule_versions
          WHERE deployment_id=NEW.deployment_id;
        IF NEW.revision <> previous_revision + 1 THEN RAISE EXCEPTION 'Schedule revision must advance once'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER schedule_version_guard BEFORE INSERT ON strategy_schedule_versions
        FOR EACH ROW EXECUTE FUNCTION guard_strategy_schedule_version();
      CREATE FUNCTION guard_strategy_schedule_tick() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM strategy_schedule_versions v WHERE v.id=NEW.version_id
          AND v.owner_id=NEW.owner_id AND v.deployment_id=NEW.deployment_id
          AND (v.payload->>'enabled')::boolean)
          THEN RAISE EXCEPTION 'Scheduled check must match enabled owned version'; END IF;
        IF NEW.run_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM strategy_runs r
          WHERE r.id=NEW.run_id AND r.owner_id=NEW.owner_id AND r.deployment_id=NEW.deployment_id
          AND r.payload->>'run_mode'='scheduled'
          AND r.payload->'request'->'schedule_context'->>'version_id'=NEW.version_id)
          THEN RAISE EXCEPTION 'Scheduled evaluation lineage must match'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER schedule_tick_guard BEFORE INSERT ON strategy_schedule_ticks
        FOR EACH ROW EXECUTE FUNCTION guard_strategy_schedule_tick();
      CREATE FUNCTION guard_strategy_schedule_state() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' AND ROW(NEW.owner_id,NEW.deployment_id) IS DISTINCT FROM ROW(OLD.owner_id,OLD.deployment_id)
          THEN RAISE EXCEPTION 'Schedule identity cannot change'; END IF;
        IF NOT EXISTS(SELECT 1 FROM strategy_schedule_versions v WHERE v.id=NEW.version_id
          AND v.owner_id=NEW.owner_id AND v.deployment_id=NEW.deployment_id
          AND ((v.payload->>'enabled')::boolean = (NEW.next_due_at IS NOT NULL))
          AND v.revision=(SELECT MAX(revision) FROM strategy_schedule_versions WHERE deployment_id=NEW.deployment_id))
          THEN RAISE EXCEPTION 'Schedule state must match latest owned version'; END IF;
        IF NEW.last_tick_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM strategy_schedule_ticks t
          WHERE t.id=NEW.last_tick_id AND t.deployment_id=NEW.deployment_id AND t.version_id=NEW.version_id)
          THEN RAISE EXCEPTION 'Last check must match current schedule'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER schedule_state_guard BEFORE INSERT OR UPDATE ON strategy_schedules
        FOR EACH ROW EXECUTE FUNCTION guard_strategy_schedule_state();
      GRANT SELECT,INSERT,UPDATE ON strategy_schedules TO tauric_app;
    """)
    for table in ("strategy_schedule_versions", "strategy_schedule_ticks"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; schedule history is immutable.")
