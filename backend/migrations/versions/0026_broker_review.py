"""Bound demo broker governance and immutable decision-to-order lineage."""

from alembic import op

revision = "0026_broker_review"
down_revision = "0025_broker_positions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE gateway_status ADD COLUMN capabilities JSON NOT NULL DEFAULT '[]'::json;
    CREATE TABLE broker_review_jobs (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      request_id VARCHAR(36) NOT NULL, assessment_job_id VARCHAR(36) NOT NULL REFERENCES analysis_jobs(id),
      profile VARCHAR(12) NOT NULL CHECK(profile IN ('cloud','local')),
      request JSON NOT NULL, context JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      status VARCHAR(20) NOT NULL CHECK(status IN ('queued','running','succeeded','failed','interrupted')),
      lease_id VARCHAR(36), created_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ,
      heartbeat_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, context_read_at TIMESTAMPTZ,
      decision_id VARCHAR(36), error VARCHAR(500), usage JSON NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE UNIQUE INDEX broker_review_active_setup ON broker_review_jobs(assessment_job_id)
      WHERE status IN ('queued','running');
    CREATE TABLE broker_review_decisions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      job_id VARCHAR(36) NOT NULL UNIQUE REFERENCES broker_review_jobs(id),
      assessment_job_id VARCHAR(36) NOT NULL UNIQUE REFERENCES analysis_jobs(id),
      native_artifact_id VARCHAR(36) NOT NULL REFERENCES analysis_artifacts(id),
      policy_version_id VARCHAR(36) NOT NULL REFERENCES broker_policy_versions(id),
      provider VARCHAR(20) NOT NULL CHECK(provider IN ('oanda','alpaca')),
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL
    );
    ALTER TABLE broker_review_jobs ADD CONSTRAINT broker_review_decision_link
      FOREIGN KEY(decision_id) REFERENCES broker_review_decisions(id);
    ALTER TABLE broker_order_intents ADD COLUMN review_decision_id VARCHAR(36) UNIQUE REFERENCES broker_review_decisions(id);
    CREATE FUNCTION guard_broker_review() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE ctx JSONB;
    BEGIN
      IF TG_TABLE_NAME='broker_review_jobs' THEN
        IF NOT EXISTS(SELECT 1 FROM analysis_jobs WHERE id=NEW.assessment_job_id AND owner_id=NEW.owner_id
            AND request->>'mode'='broker_entry_assessment' AND status='succeeded')
          THEN RAISE EXCEPTION 'Broker review must reference an owned successful setup'; END IF;
        IF TG_OP='INSERT' AND (NEW.status<>'queued' OR NEW.lease_id IS NOT NULL OR NEW.decision_id IS NOT NULL)
          THEN RAISE EXCEPTION 'Broker review must start queued'; END IF;
        IF TG_OP='UPDATE' THEN
          IF ROW(NEW.id,NEW.owner_id,NEW.request_id,NEW.assessment_job_id,NEW.profile,NEW.request::jsonb,NEW.context::jsonb,NEW.checksum,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id,OLD.owner_id,OLD.request_id,OLD.assessment_job_id,OLD.profile,OLD.request::jsonb,OLD.context::jsonb,OLD.checksum,OLD.created_at)
            THEN RAISE EXCEPTION 'Broker review authority is immutable'; END IF;
          IF OLD.status NOT IN ('queued','running') THEN RAISE EXCEPTION 'Finalized broker review is immutable'; END IF;
          IF OLD.status<>NEW.status AND NOT ((OLD.status='queued' AND NEW.status IN ('running','failed','interrupted'))
            OR (OLD.status='running' AND NEW.status IN ('succeeded','failed','interrupted')))
            THEN RAISE EXCEPTION 'Broker review transition is invalid'; END IF;
          IF OLD.status='running' AND OLD.lease_id IS DISTINCT FROM NEW.lease_id
            THEN RAISE EXCEPTION 'Broker review lease is immutable'; END IF;
        END IF;
        IF NEW.status='running' AND (NEW.lease_id IS NULL OR NEW.started_at IS NULL OR NEW.heartbeat_at IS NULL)
          THEN RAISE EXCEPTION 'Active broker review requires a lease'; END IF;
        IF NEW.status='succeeded' AND NOT EXISTS(SELECT 1 FROM broker_review_decisions WHERE id=NEW.decision_id AND job_id=NEW.id AND owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Completed broker review requires its owned decision'; END IF;
        IF NEW.status<>'succeeded' AND NEW.decision_id IS NOT NULL
          THEN RAISE EXCEPTION 'Unsuccessful broker review cannot issue a decision'; END IF;
      ELSE
        SELECT context::jsonb INTO ctx FROM broker_review_jobs WHERE id=NEW.job_id AND owner_id=NEW.owner_id
          AND assessment_job_id=NEW.assessment_job_id AND status='running';
        IF ctx IS NULL OR (ctx->>'authority') IS DISTINCT FROM 'read_only_demo_broker_entry'
          OR (ctx->'native_original'->>'id') IS DISTINCT FROM NEW.native_artifact_id
          OR (ctx->'setup_evidence'->'request'->>'provider') IS DISTINCT FROM NEW.provider
          OR (ctx->'setup_evidence'->>'policy_id') IS DISTINCT FROM NEW.policy_version_id
          OR (ctx->'native_assessment'->>'decision') IS DISTINCT FROM 'approve'
          OR (NEW.payload->>'authority') IS DISTINCT FROM 'demo_broker_entry'
          OR (NEW.payload->>'source') IS DISTINCT FROM 'openclaw'
          OR COALESCE(NEW.payload->>'outcome','') NOT IN ('allow','reject','defer')
          OR ctx->'approved_order' IS NULL
          OR (NEW.payload->'approved_order')::jsonb IS DISTINCT FROM ctx->'approved_order'
          OR (NEW.payload->>'context_checksum') IS DISTINCT FROM (SELECT checksum FROM broker_review_jobs WHERE id=NEW.job_id)
          OR (NEW.payload->>'job_id') IS DISTINCT FROM NEW.job_id
          OR (NEW.payload->>'expires_at') IS DISTINCT FROM (ctx->>'expires_at')
          OR (NEW.payload->>'account_fingerprint') IS DISTINCT FROM (ctx->'setup_evidence'->'account'->>'account_fingerprint')
          OR (NEW.payload->>'connection_revision') IS DISTINCT FROM (ctx->'setup_evidence'->>'connection_revision')
          THEN RAISE EXCEPTION 'Broker decision must preserve the bound review authority'; END IF;
        IF NOT EXISTS(SELECT 1 FROM analysis_artifacts WHERE id=NEW.native_artifact_id AND job_id=NEW.assessment_job_id AND owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Broker decision native evidence owner must match'; END IF;
        IF NOT EXISTS(SELECT 1 FROM broker_policy_versions WHERE id=NEW.policy_version_id AND provider=NEW.provider AND owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Broker decision policy owner must match'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER broker_review_guard BEFORE INSERT OR UPDATE ON broker_review_jobs FOR EACH ROW EXECUTE FUNCTION guard_broker_review();
    CREATE TRIGGER broker_review_decision_guard BEFORE INSERT ON broker_review_decisions FOR EACH ROW EXECUTE FUNCTION guard_broker_review();
    CREATE FUNCTION guard_broker_review_intent() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE decision broker_review_decisions;
    BEGIN
      IF TG_OP='UPDATE' AND NEW.review_decision_id IS DISTINCT FROM OLD.review_decision_id
        THEN RAISE EXCEPTION 'Broker order review lineage is immutable'; END IF;
      IF NEW.payload->>'source'='owner' THEN
        IF NEW.review_decision_id IS NOT NULL THEN RAISE EXCEPTION 'Owner order cannot consume a review'; END IF;
      ELSIF NEW.payload->>'source'='openclaw_broker_review' THEN
        SELECT * INTO decision FROM broker_review_decisions WHERE id=NEW.review_decision_id AND owner_id=NEW.owner_id;
        IF decision.id IS NULL OR decision.provider<>NEW.provider OR decision.policy_version_id<>NEW.policy_version_id
          OR (decision.payload->>'outcome') IS DISTINCT FROM 'allow'
          OR (NEW.payload->'order')::jsonb-'client_order_id' IS DISTINCT FROM (decision.payload->'approved_order')::jsonb-'client_order_id'
          OR NEW.connection_revision<>(decision.payload->>'connection_revision')::integer
          OR NEW.account_fingerprint<>decision.payload->>'account_fingerprint'
          THEN RAISE EXCEPTION 'Broker order must match one owned allowed review'; END IF;
      ELSE RAISE EXCEPTION 'Broker order source is unsupported'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER broker_review_intent_guard BEFORE INSERT OR UPDATE ON broker_order_intents FOR EACH ROW EXECUTE FUNCTION guard_broker_review_intent();
    GRANT SELECT,INSERT ON broker_review_jobs,broker_review_decisions TO tauric_app;
    GRANT UPDATE(status,lease_id,started_at,heartbeat_at,finished_at,context_read_at,decision_id,error,usage) ON broker_review_jobs TO tauric_app;
    """)
    for table in ("broker_review_jobs", "broker_review_decisions"):
        op.execute(
            f"CREATE TRIGGER immutable_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE ON broker_review_decisions FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
    )


def downgrade():
    raise RuntimeError(
        "Restore a verified backup; broker review and execution history is immutable."
    )
