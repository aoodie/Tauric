"""Immutable marked valuations and exact-version prospective validation."""

from alembic import op

revision = "0032_live_readiness"
down_revision = "0031_knowledge_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE trading_readiness_records (
            id VARCHAR(36) PRIMARY KEY,
            owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
            kind VARCHAR(24) NOT NULL CHECK(kind IN ('valuation','forward_protocol','forward_report')),
            parent_id VARCHAR(36) REFERENCES trading_readiness_records(id),
            request_id VARCHAR(36) NOT NULL,
            payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE(owner_id,request_id)
        );
        CREATE INDEX trading_readiness_lookup ON trading_readiness_records(owner_id,kind,created_at DESC);
        CREATE INDEX trading_readiness_parent ON trading_readiness_records(parent_id,created_at DESC);
        CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON trading_readiness_records
            FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON trading_readiness_records
            FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        CREATE FUNCTION guard_trading_readiness_parent() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.kind='forward_report') <> (NEW.parent_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Only forward reports reference a protocol';
          END IF;
          IF NEW.parent_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM trading_readiness_records WHERE id=NEW.parent_id
              AND owner_id=NEW.owner_id AND kind='forward_protocol'
          ) THEN RAISE EXCEPTION 'Forward report must reference an owned protocol'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trading_readiness_parent_guard BEFORE INSERT ON trading_readiness_records
            FOR EACH ROW EXECUTE FUNCTION guard_trading_readiness_parent();
        GRANT SELECT,INSERT ON trading_readiness_records TO tauric_app;
        CREATE FUNCTION guard_broker_decision_v2() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE ctx JSONB; scale NUMERIC; step NUMERIC; qty NUMERIC; expected_authority TEXT;
        BEGIN
          SELECT context::jsonb INTO ctx FROM broker_review_jobs
            WHERE id=NEW.job_id AND owner_id=NEW.owner_id
              AND assessment_job_id=NEW.assessment_job_id AND status='running';
          expected_authority := CASE WHEN ctx->'setup_evidence'->'account'->>'environment'='live'
            THEN 'live_broker_entry' ELSE 'demo_broker_entry' END;
          scale := COALESCE(NEW.payload->>'risk_scale','1')::numeric;
          step := (ctx->'setup_evidence'->'broker_instrument'->>'quantity_step')::numeric;
          qty := (NEW.payload->'approved_order'->>'quantity')::numeric;
          IF ctx IS NULL OR ctx->>'authority' NOT IN ('read_only_demo_broker_entry','read_only_live_broker_entry')
            OR (ctx->'native_original'->>'id') IS DISTINCT FROM NEW.native_artifact_id
            OR (ctx->'setup_evidence'->'request'->>'provider') IS DISTINCT FROM NEW.provider
            OR (ctx->'setup_evidence'->>'policy_id') IS DISTINCT FROM NEW.policy_version_id
            OR (ctx->'native_assessment'->>'decision') IS DISTINCT FROM 'approve'
            OR (NEW.payload->>'authority') IS DISTINCT FROM expected_authority
            OR (NEW.payload->>'source') IS DISTINCT FROM 'openclaw'
            OR COALESCE(NEW.payload->>'outcome','') NOT IN ('allow','reject','defer')
            OR ctx->'approved_order' IS NULL
            OR ((NEW.payload->'approved_order')::jsonb-'quantity') IS DISTINCT FROM ((ctx->'approved_order')-'quantity')
            OR scale IS NULL OR scale<=0 OR scale>1 OR step IS NULL OR step<=0
            OR qty IS NULL OR qty<>(floor((ctx->'approved_order'->>'quantity')::numeric*scale/step)*step)
            OR qty<(ctx->'setup_evidence'->'broker_instrument'->>'minimum_quantity')::numeric
            OR (NEW.payload->>'context_checksum') IS DISTINCT FROM (SELECT checksum FROM broker_review_jobs WHERE id=NEW.job_id)
            OR (NEW.payload->>'job_id') IS DISTINCT FROM NEW.job_id
            OR (NEW.payload->>'expires_at') IS DISTINCT FROM (ctx->>'expires_at')
            OR (NEW.payload->>'account_fingerprint') IS DISTINCT FROM (ctx->'setup_evidence'->'account'->>'account_fingerprint')
            OR (NEW.payload->>'connection_revision') IS DISTINCT FROM (ctx->'setup_evidence'->>'connection_revision')
            THEN RAISE EXCEPTION 'Broker decision must preserve native authority and only reduce risk'; END IF;
          IF NOT EXISTS(SELECT 1 FROM analysis_artifacts WHERE id=NEW.native_artifact_id
              AND job_id=NEW.assessment_job_id AND owner_id=NEW.owner_id)
            OR NOT EXISTS(SELECT 1 FROM broker_policy_versions WHERE id=NEW.policy_version_id
              AND provider=NEW.provider AND owner_id=NEW.owner_id)
            THEN RAISE EXCEPTION 'Broker decision native evidence and policy owners must match'; END IF;
          RETURN NEW;
        END $$;
        DROP TRIGGER broker_review_decision_guard ON broker_review_decisions;
        CREATE TRIGGER broker_review_decision_guard BEFORE INSERT ON broker_review_decisions
            FOR EACH ROW EXECUTE FUNCTION guard_broker_decision_v2();
        CREATE FUNCTION guard_live_broker_authority() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE policy JSONB;
        BEGIN
          IF TG_TABLE_NAME='broker_policy_versions' THEN
            IF NEW.payload->>'environment'='live' AND (NEW.payload->>'enabled')::boolean THEN
              IF (NEW.payload->'request'->>'live_acknowledged') IS DISTINCT FROM 'true'
                OR NOT EXISTS(SELECT 1 FROM trading_readiness_records WHERE id=NEW.payload->>'forward_report_id'
                  AND owner_id=NEW.owner_id AND kind='forward_report'
                  AND payload->>'status'='passed' AND payload->>'provider'=NEW.provider)
                THEN RAISE EXCEPTION 'Live policy requires explicit acknowledgment and owned forward evidence'; END IF;
            END IF;
          ELSE
            SELECT payload::jsonb INTO policy FROM broker_policy_versions WHERE id=NEW.policy_version_id;
            IF policy->>'environment'='live' AND (NEW.review_decision_id IS NULL
                OR (NEW.payload->>'source') IS DISTINCT FROM 'openclaw_broker_review'
                OR NEW.payload->'order'->>'stop_loss' IS NULL) THEN
              RAISE EXCEPTION 'Live intent requires native-governed lineage and a protective stop';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER live_policy_guard BEFORE INSERT ON broker_policy_versions
            FOR EACH ROW EXECUTE FUNCTION guard_live_broker_authority();
        CREATE TRIGGER live_intent_guard BEFORE INSERT ON broker_order_intents
            FOR EACH ROW EXECUTE FUNCTION guard_live_broker_authority();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; trading readiness evidence is immutable.")
