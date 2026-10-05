"""Immutable deterministic scanner configuration, evidence and research admission."""

from alembic import op

revision = "0009_scanner_candidates"
down_revision = "0008_strategy_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE scanner_profile_versions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      strategy_version_id VARCHAR(36) NOT NULL REFERENCES strategy_versions(id),
      revision INTEGER NOT NULL CHECK(revision>0), request_id VARCHAR(36) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(strategy_version_id,revision), UNIQUE(owner_id,request_id)
    );
    CREATE TABLE scanner_runs (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      request_id VARCHAR(36) NOT NULL, profile_version_id VARCHAR(36) NOT NULL REFERENCES scanner_profile_versions(id),
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE TABLE scanner_candidates (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      instrument_id VARCHAR(120) NOT NULL REFERENCES instruments(id), snapshot_id VARCHAR(36) NOT NULL REFERENCES market_snapshots(id),
      context_key VARCHAR(64) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,context_key)
    );
    CREATE TABLE scanner_matches (
      id VARCHAR(36) PRIMARY KEY, candidate_id VARCHAR(36) NOT NULL REFERENCES scanner_candidates(id),
      profile_version_id VARCHAR(36) NOT NULL REFERENCES scanner_profile_versions(id),
      expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(candidate_id,profile_version_id)
    );
    CREATE TABLE scanner_events (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      candidate_id VARCHAR(36) NOT NULL REFERENCES scanner_candidates(id), request_id VARCHAR(36) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE TABLE scanner_analyses (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      candidate_id VARCHAR(36) NOT NULL REFERENCES scanner_candidates(id),
      profile_version_id VARCHAR(36) NOT NULL REFERENCES scanner_profile_versions(id),
      strategy_version_id VARCHAR(36) NOT NULL REFERENCES strategy_versions(id), job_id VARCHAR(36) NOT NULL REFERENCES analysis_jobs(id),
      request_id VARCHAR(36) NOT NULL, research_key VARCHAR(64) NOT NULL, charged BOOLEAN NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE INDEX scanner_research_key ON scanner_analyses(research_key);
    CREATE INDEX scanner_budget ON scanner_analyses(strategy_version_id,created_at) WHERE charged;
    CREATE FUNCTION guard_scanner_profile() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous INTEGER;
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM strategy_versions WHERE id=NEW.strategy_version_id AND owner_id=NEW.owner_id)
        THEN RAISE EXCEPTION 'Scanner strategy owner must match'; END IF;
      SELECT COALESCE(MAX(revision),0) INTO previous FROM scanner_profile_versions WHERE strategy_version_id=NEW.strategy_version_id;
      IF NEW.revision<>previous+1 THEN RAISE EXCEPTION 'Scanner revision must advance once'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER scanner_profile_guard BEFORE INSERT ON scanner_profile_versions FOR EACH ROW EXECUTE FUNCTION guard_scanner_profile();
    CREATE FUNCTION guard_scanner_match() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM scanner_candidates c JOIN scanner_profile_versions p ON c.owner_id=p.owner_id WHERE c.id=NEW.candidate_id AND p.id=NEW.profile_version_id)
        THEN RAISE EXCEPTION 'Scanner match owner must agree'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER scanner_match_guard BEFORE INSERT ON scanner_matches FOR EACH ROW EXECUTE FUNCTION guard_scanner_match();
    """)
    op.execute("""
    CREATE FUNCTION guard_scanner_references() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='scanner_runs' THEN
        IF NOT EXISTS(SELECT 1 FROM scanner_profile_versions WHERE id=NEW.profile_version_id AND owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Scanner run owner must match'; END IF;
      ELSIF TG_TABLE_NAME='scanner_candidates' THEN
        IF NOT EXISTS(SELECT 1 FROM market_snapshots WHERE id=NEW.snapshot_id AND instrument_id=NEW.instrument_id)
          THEN RAISE EXCEPTION 'Scanner snapshot instrument must match'; END IF;
      ELSIF TG_TABLE_NAME='scanner_events' THEN
        IF NOT EXISTS(SELECT 1 FROM scanner_candidates WHERE id=NEW.candidate_id AND owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Scanner event owner must match'; END IF;
      ELSIF TG_TABLE_NAME='scanner_analyses' THEN
        IF NOT EXISTS(SELECT 1 FROM scanner_candidates c JOIN scanner_profile_versions p ON c.owner_id=p.owner_id JOIN analysis_jobs j ON j.owner_id=c.owner_id WHERE c.id=NEW.candidate_id AND p.id=NEW.profile_version_id AND p.strategy_version_id=NEW.strategy_version_id AND j.id=NEW.job_id AND j.instrument_id=c.instrument_id AND c.owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Scanner analysis references must match'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in ("scanner_runs", "scanner_candidates", "scanner_events", "scanner_analyses"):
        op.execute(
            f"CREATE TRIGGER scanner_references BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION guard_scanner_references()"
        )
    for table in (
        "scanner_profile_versions",
        "scanner_runs",
        "scanner_candidates",
        "scanner_matches",
        "scanner_events",
        "scanner_analyses",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; scanner history is immutable.")
