"""Independent scanner identity with optional versioned strategy links."""

from alembic import op

revision = "0023_independent_scanners"
down_revision = "0022_tradingview_mcp"
branch_labels = None
depends_on = None


def upgrade():
    # Backfill only identity metadata. Saved evidence, requests and checksums stay intact.
    op.execute("ALTER TABLE scanner_profile_versions ADD COLUMN profile_id VARCHAR(36)")
    op.execute("ALTER TABLE scanner_profile_versions DISABLE TRIGGER immutable_history")
    op.execute("UPDATE scanner_profile_versions SET profile_id=strategy_version_id")
    op.execute("ALTER TABLE scanner_profile_versions ENABLE TRIGGER immutable_history")
    op.execute("ALTER TABLE scanner_profile_versions ALTER COLUMN profile_id SET NOT NULL")
    op.execute(
        "ALTER TABLE scanner_profile_versions ALTER COLUMN strategy_version_id DROP NOT NULL"
    )
    op.execute("ALTER TABLE scanner_analyses ALTER COLUMN strategy_version_id DROP NOT NULL")
    op.execute("""ALTER TABLE scanner_profile_versions
        DROP CONSTRAINT scanner_profile_versions_strategy_version_id_revision_key,
        ADD CONSTRAINT scanner_profile_identity_revision_key UNIQUE(profile_id,revision)""")
    op.execute("DROP INDEX scanner_budget")
    op.execute(
        "CREATE INDEX scanner_budget ON scanner_analyses(profile_version_id,created_at) WHERE charged"
    )
    op.execute("""
    CREATE OR REPLACE FUNCTION guard_scanner_profile() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous INTEGER;
    BEGIN
      IF NEW.strategy_version_id IS NOT NULL AND NOT EXISTS(
        SELECT 1 FROM strategy_versions WHERE id=NEW.strategy_version_id AND owner_id=NEW.owner_id
      ) THEN RAISE EXCEPTION 'Scanner strategy owner must match'; END IF;
      IF EXISTS(SELECT 1 FROM scanner_profile_versions WHERE profile_id=NEW.profile_id AND owner_id<>NEW.owner_id)
        THEN RAISE EXCEPTION 'Scanner profile owner must match'; END IF;
      SELECT COALESCE(MAX(revision),0) INTO previous FROM scanner_profile_versions WHERE profile_id=NEW.profile_id;
      IF NEW.revision<>previous+1 THEN RAISE EXCEPTION 'Scanner revision must advance once'; END IF;
      RETURN NEW;
    END $$;
    CREATE OR REPLACE FUNCTION guard_scanner_references() RETURNS trigger LANGUAGE plpgsql AS $$
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
        IF NOT EXISTS(SELECT 1 FROM scanner_candidates c
          JOIN scanner_profile_versions p ON c.owner_id=p.owner_id
          JOIN analysis_jobs j ON j.owner_id=c.owner_id
          WHERE c.id=NEW.candidate_id AND p.id=NEW.profile_version_id
          AND p.strategy_version_id IS NOT DISTINCT FROM NEW.strategy_version_id
          AND j.id=NEW.job_id AND j.instrument_id=c.instrument_id AND c.owner_id=NEW.owner_id)
          THEN RAISE EXCEPTION 'Scanner analysis references must match'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; scanner history is immutable.")
