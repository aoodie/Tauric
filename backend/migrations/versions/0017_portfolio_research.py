"""Durable research-only portfolio schedules, findings and shared AI admission cap."""

from alembic import op

revision = "0017_portfolio_research"
down_revision = "0016_portfolio_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE shared_admission_budget_versions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      revision INTEGER NOT NULL CHECK(revision>0), request_id VARCHAR(36) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,revision), UNIQUE(owner_id,request_id),
      CHECK((payload->>'daily_job_limit')::int BETWEEN 0 AND 200)
    );
    CREATE TABLE portfolio_research_policy_versions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL, anchor_version_id VARCHAR(36) NOT NULL REFERENCES capital_scope_versions(id),
      revision INTEGER NOT NULL CHECK(revision>0), request_id VARCHAR(36) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(portfolio_id,revision), UNIQUE(owner_id,request_id)
    );
    CREATE TABLE portfolio_research_schedules (
      portfolio_id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      policy_version_id VARCHAR(36) NOT NULL REFERENCES portfolio_research_policy_versions(id),
      next_daily_due TIMESTAMPTZ, next_cadence_due TIMESTAMPTZ
    );
    CREATE TABLE portfolio_research_runs (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL, policy_version_id VARCHAR(36) NOT NULL REFERENCES portfolio_research_policy_versions(id),
      request_id VARCHAR(36) NOT NULL, trigger VARCHAR(20) NOT NULL
        CHECK(trigger IN ('daily','cadence','event','manual','chat')), trigger_key VARCHAR(200) NOT NULL,
      due_at TIMESTAMPTZ NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      status VARCHAR(20) NOT NULL CHECK(status IN ('queued','running','waiting_native','succeeded','partial','cancelled')),
      step_index INTEGER NOT NULL CHECK(step_index>=0), created_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
      UNIQUE(owner_id,request_id), UNIQUE(portfolio_id,trigger,trigger_key)
    );
    CREATE UNIQUE INDEX portfolio_research_active ON portfolio_research_runs(portfolio_id)
      WHERE status IN ('queued','running','waiting_native');
    CREATE TABLE portfolio_research_steps (
      id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL REFERENCES portfolio_research_runs(id),
      step_index INTEGER NOT NULL CHECK(step_index>=0), payload JSON NOT NULL,
      checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(run_id,step_index)
    );
    CREATE TABLE portfolio_research_links (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL, run_id VARCHAR(36) NOT NULL REFERENCES portfolio_research_runs(id),
      instrument_id VARCHAR(120) NOT NULL REFERENCES instruments(id), job_id VARCHAR(36) NOT NULL REFERENCES analysis_jobs(id),
      charged BOOLEAN NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, UNIQUE(run_id,instrument_id)
    );
    CREATE TABLE portfolio_research_digests (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL, run_id VARCHAR(36) NOT NULL UNIQUE REFERENCES portfolio_research_runs(id),
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL
    );
    CREATE FUNCTION guard_research_budget() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM shared_admission_budget_versions WHERE owner_id=NEW.owner_id)
      THEN RAISE EXCEPTION 'Budget revisions must advance once'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER research_budget_guard BEFORE INSERT ON shared_admission_budget_versions
      FOR EACH ROW EXECUTE FUNCTION guard_research_budget();
    CREATE FUNCTION guard_portfolio_research_policy() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE ref TEXT;
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM capital_scope_versions c WHERE c.id=NEW.anchor_version_id
        AND c.scope_id=NEW.portfolio_id AND c.owner_id=NEW.owner_id AND c.kind='portfolio' AND c.parent_id IS NULL)
        OR NOT EXISTS(SELECT 1 FROM portfolio_policy_versions WHERE portfolio_id=NEW.portfolio_id AND owner_id=NEW.owner_id)
        OR NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM portfolio_research_policy_versions WHERE portfolio_id=NEW.portfolio_id)
      THEN RAISE EXCEPTION 'Research requires an owned portfolio mandate and contiguous revision'; END IF;
      FOR ref IN SELECT json_array_elements_text(NEW.payload->'parameters'->'scanner_profile_ids') LOOP
        IF NOT EXISTS(SELECT 1 FROM scanner_profile_versions WHERE id=ref AND owner_id=NEW.owner_id)
        THEN RAISE EXCEPTION 'Research scanner must be owned'; END IF;
      END LOOP;
      FOR ref IN SELECT value->>'dataset_id' FROM json_array_elements(NEW.payload->'parameters'->'experiments') LOOP
        IF NOT EXISTS(SELECT 1 FROM research_records WHERE id=ref AND kind='dataset' AND owner_id=NEW.owner_id)
        THEN RAISE EXCEPTION 'Experiment dataset must be owned'; END IF;
      END LOOP;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_research_policy_guard BEFORE INSERT ON portfolio_research_policy_versions
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_research_policy();
    CREATE FUNCTION guard_portfolio_research_schedule() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND (NEW.owner_id,NEW.portfolio_id) IS DISTINCT FROM (OLD.owner_id,OLD.portfolio_id)
      THEN RAISE EXCEPTION 'Research schedule identity is immutable'; END IF;
      IF NOT EXISTS(SELECT 1 FROM portfolio_research_policy_versions p WHERE p.id=NEW.policy_version_id
        AND p.owner_id=NEW.owner_id AND p.portfolio_id=NEW.portfolio_id
        AND p.revision=(SELECT MAX(revision) FROM portfolio_research_policy_versions WHERE portfolio_id=NEW.portfolio_id)
        AND (p.payload->'parameters'->>'enabled')::boolean=(NEW.next_daily_due IS NOT NULL))
      THEN RAISE EXCEPTION 'Research schedule must follow its current policy'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_research_schedule_guard BEFORE INSERT OR UPDATE ON portfolio_research_schedules
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_research_schedule();
    CREATE FUNCTION guard_portfolio_research_run() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' THEN
        IF OLD.status IN ('succeeded','partial','cancelled') OR
          (NEW.id,NEW.owner_id,NEW.portfolio_id,NEW.policy_version_id,NEW.request_id,NEW.trigger,NEW.trigger_key,
           NEW.due_at,NEW.payload::text,NEW.checksum,NEW.created_at) IS DISTINCT FROM
          (OLD.id,OLD.owner_id,OLD.portfolio_id,OLD.policy_version_id,OLD.request_id,OLD.trigger,OLD.trigger_key,
           OLD.due_at,OLD.payload::text,OLD.checksum,OLD.created_at)
        THEN RAISE EXCEPTION 'Research input and terminal runs are immutable'; END IF;
      END IF;
      IF NOT EXISTS(SELECT 1 FROM portfolio_research_policy_versions p WHERE p.id=NEW.policy_version_id
        AND p.owner_id=NEW.owner_id AND p.portfolio_id=NEW.portfolio_id
        AND p.payload->'parameters' IS NOT NULL
        AND (p.payload->'parameters')::jsonb=(NEW.payload->'parameters')::jsonb)
      THEN RAISE EXCEPTION 'Research run must retain its owned policy parameters'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_research_run_guard BEFORE INSERT OR UPDATE ON portfolio_research_runs
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_research_run();
    CREATE FUNCTION guard_portfolio_research_link() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM portfolio_research_runs r JOIN analysis_jobs j ON j.id=NEW.job_id
        WHERE r.id=NEW.run_id AND r.owner_id=NEW.owner_id AND r.portfolio_id=NEW.portfolio_id
          AND j.owner_id=NEW.owner_id AND j.instrument_id=NEW.instrument_id)
      THEN RAISE EXCEPTION 'Research admission lineage must match'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_research_link_guard BEFORE INSERT ON portfolio_research_links
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_research_link();
    CREATE FUNCTION guard_portfolio_research_digest() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM portfolio_research_runs r WHERE r.id=NEW.run_id
        AND r.owner_id=NEW.owner_id AND r.portfolio_id=NEW.portfolio_id AND r.status IN ('succeeded','partial','cancelled'))
        OR (NEW.payload->>'execution_authority')::boolean IS DISTINCT FROM false
      THEN RAISE EXCEPTION 'Digest must reference a terminal owned research-only run'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_research_digest_guard BEFORE INSERT ON portfolio_research_digests
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_research_digest();
    GRANT SELECT,INSERT,UPDATE ON portfolio_research_schedules TO tauric_app;
    GRANT SELECT,INSERT ON portfolio_research_runs TO tauric_app;
    GRANT UPDATE(status,step_index,finished_at) ON portfolio_research_runs TO tauric_app;
    ALTER TABLE portfolio_action_receipts DROP CONSTRAINT portfolio_action_receipts_action_check;
    ALTER TABLE portfolio_action_receipts ADD CONSTRAINT portfolio_action_receipts_action_check
      CHECK(action IN ('preferences','pause','resume','research_settings'));
    """)
    for table in (
        "shared_admission_budget_versions",
        "portfolio_research_policy_versions",
        "portfolio_research_steps",
        "portfolio_research_links",
        "portfolio_research_digests",
    ):
        op.execute(f"""
          CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
          CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
          GRANT SELECT,INSERT ON {table} TO tauric_app;
        """)


def downgrade():
    raise RuntimeError("Restore a verified backup; portfolio research evidence is immutable.")
