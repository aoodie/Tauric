"""Separate frozen Lab inputs from append-only original research records."""

from alembic import op

revision = "0034_quant_lab"
down_revision = "0033_worker_progress"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE quant_records (
          id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
          kind VARCHAR(20) NOT NULL CHECK(kind IN ('session_report','quant_job')),
          request_id VARCHAR(36) NOT NULL, parent_id VARCHAR(36) NOT NULL REFERENCES research_records(id),
          name VARCHAR(120) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,kind,request_id)
        );
        CREATE INDEX quant_owner_time ON quant_records(owner_id,kind,created_at);
        CREATE FUNCTION guard_quant_record() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE before_status TEXT; after_status TEXT;
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NOT EXISTS(SELECT 1 FROM research_records d WHERE d.id=NEW.parent_id
              AND d.owner_id=NEW.owner_id AND d.kind='dataset') THEN
              RAISE EXCEPTION 'Lab requires an owned saved dataset';
            END IF;
            IF NEW.kind='quant_job' AND NEW.payload->>'status' IS DISTINCT FROM 'queued' THEN
              RAISE EXCEPTION 'Lab jobs begin queued';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.kind <> 'quant_job' OR ROW(OLD.id,OLD.owner_id,OLD.kind,OLD.request_id,OLD.parent_id,OLD.name,OLD.created_at)
            IS DISTINCT FROM ROW(NEW.id,NEW.owner_id,NEW.kind,NEW.request_id,NEW.parent_id,NEW.name,NEW.created_at) THEN
            RAISE EXCEPTION 'Lab input identity is immutable';
          END IF;
          IF (OLD.payload::jsonb - ARRAY['status','result','error','claim_token','lease_at','started_at','finished_at'])
            IS DISTINCT FROM (NEW.payload::jsonb - ARRAY['status','result','error','claim_token','lease_at','started_at','finished_at']) THEN
            RAISE EXCEPTION 'Lab input evidence is immutable';
          END IF;
          before_status := OLD.payload->>'status'; after_status := NEW.payload->>'status';
          IF NOT ((before_status='queued' AND after_status IN ('running','cancelled','failed'))
              OR (before_status='running' AND after_status IN ('running','completed','cancelled','failed'))) THEN
            RAISE EXCEPTION 'Invalid Lab state transition';
          END IF;
          IF after_status <> 'completed' AND NEW.payload->>'result' IS NOT NULL THEN
            RAISE EXCEPTION 'Only completed Lab jobs have results';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER quant_input_guard BEFORE INSERT OR UPDATE ON quant_records FOR EACH ROW EXECUTE FUNCTION guard_quant_record();
        CREATE TRIGGER immutable_history BEFORE DELETE ON quant_records FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON quant_records FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        GRANT SELECT,INSERT ON quant_records TO tauric_app;
        GRANT UPDATE(payload,checksum) ON quant_records TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; Lab evidence is immutable")
