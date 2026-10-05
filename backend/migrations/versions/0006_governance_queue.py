"""Private model-backed governance with immutable requests and bounded leases."""

import json

from alembic import op
from sqlalchemy import text

revision = "0006_governance_queue"
down_revision = "0005_paper_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE governance_jobs (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        request_id VARCHAR(36) NOT NULL, proposal_id VARCHAR(36) NOT NULL REFERENCES trading_records(id),
        profile VARCHAR(12) NOT NULL CHECK(profile IN ('cloud','local')),
        request JSON NOT NULL, context JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
        status VARCHAR(20) NOT NULL CHECK(status IN ('queued','running','succeeded','failed','interrupted')),
        lease_id VARCHAR(36), created_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ,
        heartbeat_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, context_read_at TIMESTAMPTZ,
        decision_id VARCHAR(36) REFERENCES trading_records(id), error VARCHAR(500), usage JSON NOT NULL,
        UNIQUE(owner_id,request_id)
      );
      CREATE UNIQUE INDEX governance_active_proposal ON governance_jobs(proposal_id)
        WHERE status IN ('queued','running');
      CREATE TABLE gateway_status (
        id VARCHAR(20) PRIMARY KEY, fingerprint VARCHAR(64) NOT NULL, heartbeat_at TIMESTAMPTZ NOT NULL
      );
      CREATE FUNCTION guard_governance_job() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP <> 'UPDATE' THEN RAISE EXCEPTION 'Governance history cannot be removed'; END IF;
        IF ROW(NEW.id,NEW.owner_id,NEW.request_id,NEW.proposal_id,NEW.profile,NEW.request::jsonb,
               NEW.context::jsonb,NEW.checksum,NEW.created_at)
           IS DISTINCT FROM ROW(OLD.id,OLD.owner_id,OLD.request_id,OLD.proposal_id,OLD.profile,
               OLD.request::jsonb,OLD.context::jsonb,OLD.checksum,OLD.created_at)
        THEN RAISE EXCEPTION 'Governance request is immutable'; END IF;
        IF OLD.status NOT IN ('queued','running') THEN RAISE EXCEPTION 'Finalized governance is immutable'; END IF;
        IF OLD.status = 'running' AND NEW.status = 'queued' THEN RAISE EXCEPTION 'Billed work cannot be silently retried'; END IF;
        IF OLD.status = 'running' AND NEW.lease_id IS DISTINCT FROM OLD.lease_id
          THEN RAISE EXCEPTION 'Lease identity cannot change'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER governance_guard BEFORE UPDATE OR DELETE ON governance_jobs
        FOR EACH ROW EXECUTE FUNCTION guard_governance_job();
      CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON governance_jobs
        FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
      GRANT SELECT,INSERT ON governance_jobs TO tauric_app;
      GRANT UPDATE(status,lease_id,started_at,heartbeat_at,finished_at,decision_id,error,context_read_at,usage) ON governance_jobs TO tauric_app;
      GRANT SELECT,INSERT,UPDATE ON gateway_status TO tauric_app;
    """)
    definitions = [
        ("cloud_model_tier", "Cloud review model", "enum", "deep", ["quick", "deep"], None, None),
        ("local_model_tier", "Local review model", "enum", "deep", ["quick", "deep"], None, None),
        ("timeout_seconds", "Review time budget (seconds)", "integer", 180, [], 30, 600),
        (
            "max_output_tokens",
            "Model output limit (tokens per call)",
            "integer",
            2000,
            [],
            512,
            8000,
        ),
    ]
    for key, label, kind, default, options, minimum, maximum in definitions:
        value = dict(
            key="governance." + key,
            label=label,
            kind=kind,
            default=default,
            description="Applies to the next OpenClaw review. Changes invalidate pending reviews.",
            scopes=["global", "user"],
            options=options,
            minimum=minimum,
            maximum=maximum,
            restart_required=False,
        )
        op.get_bind().execute(
            text(
                "INSERT INTO setting_definitions(key,definition) VALUES (:key,CAST(:value AS JSON))"
            ),
            {"key": value["key"], "value": json.dumps(value)},
        )


def downgrade():
    raise RuntimeError("Restore a verified backup; governance history is immutable.")
