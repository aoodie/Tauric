"""Immutable exact-version experiment protocols, evaluations and qualification reports."""

from alembic import op

revision = "0018_strategy_qualification"
down_revision = "0017_portfolio_research"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE qualification_protocols (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL, portfolio_version_id VARCHAR(36) NOT NULL REFERENCES portfolio_policy_versions(id),
      strategy_version_id VARCHAR(36) NOT NULL REFERENCES strategy_versions(id), dataset_id VARCHAR(36) NOT NULL REFERENCES research_records(id),
      request_id VARCHAR(36) NOT NULL, name VARCHAR(120) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE TABLE qualification_evaluations (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id), protocol_id VARCHAR(36) NOT NULL UNIQUE REFERENCES qualification_protocols(id),
      request_id VARCHAR(36) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,request_id)
    );
    CREATE TABLE qualification_admissions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id), protocol_id VARCHAR(36) NOT NULL REFERENCES qualification_protocols(id),
      evaluation_id VARCHAR(36) NOT NULL REFERENCES qualification_evaluations(id), job_id VARCHAR(36) NOT NULL REFERENCES analysis_jobs(id),
      request_id VARCHAR(36) NOT NULL, context_key VARCHAR(64) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id), UNIQUE(evaluation_id,context_key)
    );
    CREATE TABLE qualification_reports (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id), protocol_id VARCHAR(36) NOT NULL REFERENCES qualification_protocols(id),
      evaluation_id VARCHAR(36) NOT NULL REFERENCES qualification_evaluations(id), artifact_id VARCHAR(36) REFERENCES analysis_artifacts(id),
      request_id VARCHAR(36) NOT NULL, revision INTEGER NOT NULL CHECK(revision>0), payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id), UNIQUE(protocol_id,revision), CHECK((payload->>'execution_authority')::boolean IS NOT DISTINCT FROM false)
    );
    CREATE FUNCTION guard_qualification_protocol() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM portfolio_policy_versions p JOIN strategy_versions s ON s.id=NEW.strategy_version_id
        JOIN research_records d ON d.id=NEW.dataset_id WHERE p.id=NEW.portfolio_version_id AND p.portfolio_id=NEW.portfolio_id
        AND p.owner_id=NEW.owner_id AND s.owner_id=NEW.owner_id AND d.owner_id=NEW.owner_id AND d.kind='dataset')
      THEN RAISE EXCEPTION 'Qualification must reference owned portfolio, strategy and dataset'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER qualification_protocol_guard BEFORE INSERT ON qualification_protocols FOR EACH ROW EXECUTE FUNCTION guard_qualification_protocol();
    CREATE FUNCTION guard_qualification_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM qualification_protocols p WHERE p.id=NEW.protocol_id AND p.owner_id=NEW.owner_id)
      THEN RAISE EXCEPTION 'Qualification evidence must be owned'; END IF;
      IF TG_TABLE_NAME='qualification_admissions' OR TG_TABLE_NAME='qualification_reports' THEN
        IF NOT EXISTS(SELECT 1 FROM qualification_evaluations e WHERE e.id=NEW.evaluation_id AND e.protocol_id=NEW.protocol_id AND e.owner_id=NEW.owner_id)
        THEN RAISE EXCEPTION 'Qualification evaluation lineage mismatch'; END IF;
      END IF;
      IF TG_TABLE_NAME='qualification_admissions' THEN
        IF NOT EXISTS(SELECT 1 FROM analysis_jobs j WHERE j.id=NEW.job_id AND j.owner_id=NEW.owner_id
          AND j.request->>'mode'='strategy_qualification' AND j.request->'qualification_context'->>'evaluation_id'=NEW.evaluation_id)
        THEN RAISE EXCEPTION 'Qualification requires a dedicated native assessment admission'; END IF;
      ELSIF TG_TABLE_NAME='qualification_reports' THEN
        IF NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM qualification_reports WHERE protocol_id=NEW.protocol_id)
        THEN RAISE EXCEPTION 'Qualification reports require contiguous revisions'; END IF;
        IF NEW.artifact_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM analysis_artifacts a JOIN qualification_admissions q ON q.job_id=a.job_id
          WHERE a.id=NEW.artifact_id AND a.owner_id=NEW.owner_id AND q.protocol_id=NEW.protocol_id AND q.evaluation_id=NEW.evaluation_id)
        THEN RAISE EXCEPTION 'Native qualification artifact lineage mismatch'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in (
        "qualification_protocols",
        "qualification_evaluations",
        "qualification_admissions",
        "qualification_reports",
    ):
        op.execute(f"""
        CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        GRANT SELECT,INSERT ON {table} TO tauric_app;
        """)
        if table != "qualification_protocols":
            op.execute(
                f"CREATE TRIGGER qualification_evidence_guard BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION guard_qualification_evidence();"
            )


def downgrade():
    raise RuntimeError("Restore a verified backup; qualification evidence is immutable.")
