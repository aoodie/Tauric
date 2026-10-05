"""Opt-in, versioned demo routes and durable closed-bar signal workflows."""

from alembic import op

revision = "0028_broker_routes"
down_revision = "0027_instrument_discovery"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE broker_route_versions (
        id VARCHAR(36) PRIMARY KEY, route_id VARCHAR(36) NOT NULL,
        owner_id VARCHAR(36) NOT NULL REFERENCES owners(id), revision INT NOT NULL CHECK(revision>0),
        request_id VARCHAR(36) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, UNIQUE(route_id,revision), UNIQUE(owner_id,request_id),
        CHECK(json_typeof(payload->'request'->'enabled')='boolean'),
        CHECK((payload->'request'->>'risk_percent')::numeric>0 AND (payload->'request'->>'risk_percent')::numeric<=100),
        CHECK((payload->'request'->>'cadence_seconds')::int BETWEEN 30 AND 3600)
      );
      CREATE TABLE broker_routes (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        version_id VARCHAR(36) NOT NULL REFERENCES broker_route_versions(id),
        next_due_at TIMESTAMPTZ, last_event_id VARCHAR(36)
      );
      CREATE INDEX broker_route_due ON broker_routes(next_due_at) WHERE next_due_at IS NOT NULL;
      CREATE TABLE broker_route_workflows (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        route_id VARCHAR(36) NOT NULL REFERENCES broker_routes(id),
        version_id VARCHAR(36) NOT NULL REFERENCES broker_route_versions(id),
        signal_key VARCHAR(64) NOT NULL, assessment_job_id VARCHAR(36) NOT NULL UNIQUE REFERENCES analysis_jobs(id),
        review_job_id VARCHAR(36) UNIQUE REFERENCES broker_review_jobs(id),
        intent_id VARCHAR(36) UNIQUE REFERENCES broker_order_intents(id),
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
        status VARCHAR(24) NOT NULL CHECK(status IN ('native_pending','review_pending','entry_pending','tracking','closed','blocked','cancelled')),
        next_due_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,signal_key),
        CHECK((status IN ('closed','blocked','cancelled'))=(next_due_at IS NULL))
      );
      CREATE INDEX broker_workflow_due ON broker_route_workflows(next_due_at) WHERE next_due_at IS NOT NULL;
      CREATE TABLE broker_route_events (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        route_id VARCHAR(36) NOT NULL REFERENCES broker_routes(id),
        version_id VARCHAR(36) NOT NULL REFERENCES broker_route_versions(id),
        workflow_id VARCHAR(36) REFERENCES broker_route_workflows(id),
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL
      );
      ALTER TABLE broker_routes ADD FOREIGN KEY(last_event_id) REFERENCES broker_route_events(id);
      CREATE INDEX broker_route_history ON broker_route_events(route_id,created_at);
      CREATE FUNCTION guard_broker_route_version() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM strategy_versions v JOIN instruments i ON i.id=NEW.payload->'request'->>'instrument_id'
          WHERE v.id=NEW.payload->'request'->>'strategy_version_id' AND v.owner_id=NEW.owner_id
          AND v.payload->'parameters'->'rules'->>'engine'='closed_bar_breakout_v1'
          AND ((i.definition->>'asset_type'='fx' AND NEW.payload->'request'->>'provider'='oanda')
            OR (i.definition->>'asset_type' IN ('equity','etf') AND NEW.payload->'request'->>'provider'='alpaca')))
          THEN RAISE EXCEPTION 'Broker route requires an owned executable strategy and matching instrument'; END IF;
        IF EXISTS(SELECT 1 FROM broker_route_versions WHERE route_id=NEW.route_id AND owner_id<>NEW.owner_id)
          OR NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM broker_route_versions WHERE route_id=NEW.route_id)
          THEN RAISE EXCEPTION 'Broker route revision or owner mismatch'; END IF;
        IF (NEW.payload->'request'->>'enabled')::boolean AND NOT EXISTS(
          SELECT 1 FROM broker_policy_versions p WHERE p.id=NEW.payload->>'policy_id'
          AND p.owner_id=NEW.owner_id AND p.provider=NEW.payload->'request'->>'provider'
          AND (p.payload->>'enabled')::boolean AND p.payload->>'account_fingerprint'=NEW.payload->>'account_fingerprint'
          AND p.payload->>'connection_revision'=NEW.payload->>'connection_revision')
          THEN RAISE EXCEPTION 'Enabled route must match an enabled owned demo policy'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER broker_route_version_guard BEFORE INSERT ON broker_route_versions FOR EACH ROW EXECUTE FUNCTION guard_broker_route_version();
      CREATE FUNCTION guard_broker_route_cursor() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' AND ROW(NEW.id,NEW.owner_id) IS DISTINCT FROM ROW(OLD.id,OLD.owner_id)
          THEN RAISE EXCEPTION 'Broker route identity is immutable'; END IF;
        IF NOT EXISTS(SELECT 1 FROM broker_route_versions v WHERE v.id=NEW.version_id AND v.route_id=NEW.id AND v.owner_id=NEW.owner_id
          AND v.revision=(SELECT MAX(revision) FROM broker_route_versions WHERE route_id=NEW.id)
          AND ((v.payload->'request'->>'enabled')::boolean=(NEW.next_due_at IS NOT NULL)))
          THEN RAISE EXCEPTION 'Broker route must point to latest owned version'; END IF;
        IF NEW.last_event_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM broker_route_events e
          WHERE e.id=NEW.last_event_id AND e.route_id=NEW.id AND e.owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Broker route check receipt mismatch'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER broker_route_cursor_guard BEFORE INSERT OR UPDATE ON broker_routes FOR EACH ROW EXECUTE FUNCTION guard_broker_route_cursor();
      CREATE FUNCTION guard_broker_route_workflow() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF ROW(NEW.id,NEW.owner_id,NEW.route_id,NEW.version_id,NEW.signal_key,NEW.assessment_job_id,NEW.payload::text,NEW.checksum,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.owner_id,OLD.route_id,OLD.version_id,OLD.signal_key,OLD.assessment_job_id,OLD.payload::text,OLD.checksum,OLD.created_at)
            OR (OLD.review_job_id IS NOT NULL AND NEW.review_job_id IS DISTINCT FROM OLD.review_job_id)
            OR (OLD.intent_id IS NOT NULL AND NEW.intent_id IS DISTINCT FROM OLD.intent_id)
            OR (OLD.status IN ('closed','blocked','cancelled') AND NEW IS DISTINCT FROM OLD)
            THEN RAISE EXCEPTION 'Broker workflow evidence and terminal state are immutable'; END IF;
        ELSE
          IF NEW.status<>'native_pending' OR NEW.review_job_id IS NOT NULL OR NEW.intent_id IS NOT NULL
            THEN RAISE EXCEPTION 'Broker workflow starts with native assessment'; END IF;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM broker_route_versions v JOIN broker_routes r ON r.id=v.route_id
          JOIN analysis_jobs j ON j.id=NEW.assessment_job_id
          WHERE v.id=NEW.version_id AND v.owner_id=NEW.owner_id AND r.owner_id=NEW.owner_id AND r.id=NEW.route_id
          AND j.owner_id=NEW.owner_id AND j.request->>'mode'='broker_entry_assessment'
          AND j.request->'broker_context'->'assessment_input'->'route_context'->>'version_id'=NEW.version_id
          AND j.request->'broker_context'->'assessment_input'->'route_context'->>'workflow_id'=NEW.id
          AND j.request->'broker_context'->'assessment_input'->'route_context'->>'signal_key'=NEW.signal_key)
          THEN RAISE EXCEPTION 'Broker workflow native lineage mismatch'; END IF;
        IF NEW.review_job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM broker_review_jobs j
          WHERE j.id=NEW.review_job_id AND j.owner_id=NEW.owner_id AND j.assessment_job_id=NEW.assessment_job_id
          AND (j.request->>'submit_on_allow')::boolean)
          THEN RAISE EXCEPTION 'Broker workflow review lineage mismatch'; END IF;
        IF NEW.intent_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM broker_order_intents i JOIN broker_review_decisions d ON d.id=i.review_decision_id
          WHERE i.id=NEW.intent_id AND i.owner_id=NEW.owner_id AND d.job_id=NEW.review_job_id AND d.assessment_job_id=NEW.assessment_job_id)
          THEN RAISE EXCEPTION 'Broker workflow intent lineage mismatch'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER broker_workflow_guard BEFORE INSERT OR UPDATE ON broker_route_workflows FOR EACH ROW EXECUTE FUNCTION guard_broker_route_workflow();
      CREATE FUNCTION guard_broker_route_event() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM broker_route_versions v WHERE v.id=NEW.version_id AND v.route_id=NEW.route_id AND v.owner_id=NEW.owner_id)
          OR (NEW.workflow_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM broker_route_workflows w WHERE w.id=NEW.workflow_id AND w.owner_id=NEW.owner_id AND w.route_id=NEW.route_id AND w.version_id=NEW.version_id))
          THEN RAISE EXCEPTION 'Broker route receipt lineage mismatch'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER broker_route_event_guard BEFORE INSERT ON broker_route_events FOR EACH ROW EXECUTE FUNCTION guard_broker_route_event();
      GRANT SELECT,INSERT ON broker_routes,broker_route_workflows TO tauric_app;
      GRANT UPDATE(version_id,next_due_at,last_event_id) ON broker_routes TO tauric_app;
      GRANT UPDATE(review_job_id,intent_id,status,next_due_at) ON broker_route_workflows TO tauric_app;
    """)
    for table in ("broker_route_versions", "broker_route_events"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; broker route history is immutable.")
