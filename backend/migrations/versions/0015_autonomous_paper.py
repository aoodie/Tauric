"""Opt-in paper execution policies, durable workflows and immutable receipts."""

from alembic import op

revision = "0015_autonomous_paper"
down_revision = "0014_strategy_schedules"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE autonomy_policy_versions (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id), revision INTEGER NOT NULL CHECK(revision>0),
        request_id VARCHAR(36) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, UNIQUE(deployment_id,revision), UNIQUE(owner_id,request_id)
      );
      CREATE TABLE autonomy_workflows (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id),
        policy_version_id VARCHAR(36) NOT NULL REFERENCES autonomy_policy_versions(id),
        initial_signal_id VARCHAR(36) NOT NULL REFERENCES strategy_runs(id), signal_key VARCHAR(64) NOT NULL,
        kind VARCHAR(8) NOT NULL CHECK(kind IN ('entry','exit')),
        status VARCHAR(24) NOT NULL CHECK(status IN ('waiting_research','waiting_review','waiting_exit','opened','closed','blocked','cancelled')),
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, next_check_at TIMESTAMPTZ,
        analysis_job_id VARCHAR(36) REFERENCES analysis_jobs(id), native_artifact_id VARCHAR(36) REFERENCES analysis_artifacts(id),
        review_job_id VARCHAR(36) REFERENCES governance_jobs(id),
        proposal_id VARCHAR(36) REFERENCES trading_records(id), decision_id VARCHAR(36) REFERENCES trading_records(id),
        plan_id VARCHAR(36) REFERENCES trading_records(id), entry_fill_id VARCHAR(36) REFERENCES trading_records(id),
        close_fill_id VARCHAR(36) REFERENCES trading_records(id), reason VARCHAR(500) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, UNIQUE(deployment_id,signal_key,kind)
      );
      CREATE INDEX autonomy_due ON autonomy_workflows(next_check_at) WHERE next_check_at IS NOT NULL;
      CREATE TABLE autonomy_events (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id),
        workflow_id VARCHAR(36) NOT NULL REFERENCES autonomy_workflows(id), kind VARCHAR(32) NOT NULL,
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL
      );
      CREATE INDEX autonomy_event_history ON autonomy_events(deployment_id,created_at);
      CREATE FUNCTION guard_autonomy_policy() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE previous_revision INTEGER;
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM paper_deployments d JOIN strategy_versions v ON v.id=d.payload->>'strategy_revision_id'
          WHERE d.id=NEW.deployment_id AND d.owner_id=NEW.owner_id AND v.owner_id=NEW.owner_id
          AND v.payload->'parameters'->'rules'->>'engine'='closed_bar_breakout_v1')
          THEN RAISE EXCEPTION 'Autonomy requires an owned executable paper deployment'; END IF;
        SELECT COALESCE(MAX(revision),0) INTO previous_revision FROM autonomy_policy_versions WHERE deployment_id=NEW.deployment_id;
        IF NEW.revision<>previous_revision+1 THEN RAISE EXCEPTION 'Autonomy revisions must advance once'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER autonomy_policy_guard BEFORE INSERT ON autonomy_policy_versions FOR EACH ROW EXECUTE FUNCTION guard_autonomy_policy();
      CREATE FUNCTION guard_autonomy_workflow() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE ref VARCHAR(36); expected_kind TEXT;
      BEGIN
        IF TG_OP='UPDATE' AND ROW(NEW.id,NEW.owner_id,NEW.deployment_id,NEW.policy_version_id,NEW.initial_signal_id,NEW.signal_key,NEW.kind,NEW.checksum,NEW.created_at,NEW.payload::text)
          IS DISTINCT FROM ROW(OLD.id,OLD.owner_id,OLD.deployment_id,OLD.policy_version_id,OLD.initial_signal_id,OLD.signal_key,OLD.kind,OLD.checksum,OLD.created_at,OLD.payload::text)
          THEN RAISE EXCEPTION 'Workflow admission evidence is immutable'; END IF;
        IF TG_OP='UPDATE' AND OLD.status IN ('closed','blocked','cancelled')
          THEN RAISE EXCEPTION 'Terminal autonomous workflows cannot restart'; END IF;
        IF TG_OP='UPDATE' THEN
          IF NOT (NEW.status=OLD.status
            OR OLD.status='waiting_research' AND NEW.status IN ('waiting_review','blocked','cancelled')
            OR OLD.status='waiting_review' AND NEW.status IN ('opened','blocked','cancelled')
            OR OLD.status='waiting_exit' AND NEW.status IN ('closed','blocked','cancelled')
            OR OLD.status='opened' AND NEW.status='closed')
            THEN RAISE EXCEPTION 'Invalid autonomous workflow transition'; END IF;
          IF (OLD.analysis_job_id IS NOT NULL AND NEW.analysis_job_id IS DISTINCT FROM OLD.analysis_job_id)
            OR (OLD.native_artifact_id IS NOT NULL AND NEW.native_artifact_id IS DISTINCT FROM OLD.native_artifact_id)
            OR (OLD.review_job_id IS NOT NULL AND NEW.review_job_id IS DISTINCT FROM OLD.review_job_id)
            OR (OLD.proposal_id IS NOT NULL AND NEW.proposal_id IS DISTINCT FROM OLD.proposal_id)
            OR (OLD.decision_id IS NOT NULL AND NEW.decision_id IS DISTINCT FROM OLD.decision_id)
            OR (OLD.plan_id IS NOT NULL AND NEW.plan_id IS DISTINCT FROM OLD.plan_id)
            OR (OLD.entry_fill_id IS NOT NULL AND NEW.entry_fill_id IS DISTINCT FROM OLD.entry_fill_id)
            OR (OLD.close_fill_id IS NOT NULL AND NEW.close_fill_id IS DISTINCT FROM OLD.close_fill_id)
            THEN RAISE EXCEPTION 'Committed workflow references cannot be rebound'; END IF;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM autonomy_policy_versions p JOIN strategy_runs r ON r.id=NEW.initial_signal_id
          WHERE p.id=NEW.policy_version_id AND p.deployment_id=NEW.deployment_id AND p.owner_id=NEW.owner_id
          AND r.owner_id=NEW.owner_id AND r.deployment_id=NEW.deployment_id AND r.payload->>'run_mode'='scheduled'
          AND r.action=CASE NEW.kind WHEN 'entry' THEN 'enter' ELSE 'exit' END)
          THEN RAISE EXCEPTION 'Workflow must bind owned policy and scheduled signal'; END IF;
        IF NEW.analysis_job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM analysis_jobs j JOIN paper_deployments d ON d.id=NEW.deployment_id
          WHERE j.id=NEW.analysis_job_id AND j.owner_id=NEW.owner_id AND j.instrument_id=d.instrument_id)
          THEN RAISE EXCEPTION 'Workflow research must match owner/instrument'; END IF;
        IF NEW.native_artifact_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM analysis_artifacts a
          WHERE a.id=NEW.native_artifact_id AND a.owner_id=NEW.owner_id AND a.job_id=NEW.analysis_job_id)
          THEN RAISE EXCEPTION 'Workflow original report must match research'; END IF;
        FOREACH expected_kind IN ARRAY ARRAY['proposal','governance','plan'] LOOP
          ref:=CASE expected_kind WHEN 'proposal' THEN NEW.proposal_id WHEN 'governance' THEN NEW.decision_id WHEN 'plan' THEN NEW.plan_id ELSE NULL END;
          IF ref IS NOT NULL AND NOT EXISTS(SELECT 1 FROM trading_records t WHERE t.id=ref AND t.kind=expected_kind
            AND t.owner_id=NEW.owner_id AND t.deployment_id=NEW.deployment_id)
            THEN RAISE EXCEPTION 'Workflow financial reference must match'; END IF;
        END LOOP;
        FOREACH ref IN ARRAY ARRAY[NEW.entry_fill_id,NEW.close_fill_id] LOOP
          IF ref IS NOT NULL AND NOT EXISTS(SELECT 1 FROM trading_records t WHERE t.id=ref AND t.kind='fill'
            AND t.owner_id=NEW.owner_id AND t.deployment_id=NEW.deployment_id)
            THEN RAISE EXCEPTION 'Workflow fill reference must match'; END IF;
        END LOOP;
        IF NEW.review_job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM governance_jobs g WHERE g.id=NEW.review_job_id
          AND g.owner_id=NEW.owner_id AND g.proposal_id=NEW.proposal_id)
          THEN RAISE EXCEPTION 'Workflow review must match proposal'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER autonomy_workflow_guard BEFORE INSERT OR UPDATE ON autonomy_workflows FOR EACH ROW EXECUTE FUNCTION guard_autonomy_workflow();
      CREATE FUNCTION guard_autonomy_event() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM autonomy_workflows w WHERE w.id=NEW.workflow_id AND w.owner_id=NEW.owner_id AND w.deployment_id=NEW.deployment_id)
          THEN RAISE EXCEPTION 'Autonomy event must match owned workflow'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER autonomy_event_guard BEFORE INSERT ON autonomy_events FOR EACH ROW EXECUTE FUNCTION guard_autonomy_event();
      GRANT SELECT,INSERT,UPDATE ON autonomy_workflows TO tauric_app;
    """)
    for table in ("autonomy_policy_versions", "autonomy_events"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; autonomous paper history is immutable.")
