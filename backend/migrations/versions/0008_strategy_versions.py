"""Owner strategy versions and append-only deployment pause controls."""

from alembic import op

revision = "0008_strategy_versions"
down_revision = "0007_capital_scopes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE strategy_versions (
        id VARCHAR(36) PRIMARY KEY, strategy_id VARCHAR(36) NOT NULL,
        owner_id VARCHAR(36) NOT NULL REFERENCES owners(id), name VARCHAR(80) NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0), request_id VARCHAR(36) NOT NULL,
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(strategy_id, revision), UNIQUE(owner_id, request_id)
      );
      CREATE TABLE deployment_control_versions (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        deployment_id VARCHAR(36) NOT NULL REFERENCES paper_deployments(id),
        revision INTEGER NOT NULL CHECK(revision > 0), payload JSON NOT NULL,
        checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(deployment_id, revision)
      );
      CREATE FUNCTION guard_strategy_insert() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE previous strategy_versions;
      BEGIN
        IF NEW.revision = 1 THEN
          IF NEW.strategy_id <> NEW.id THEN RAISE EXCEPTION 'Initial strategy identity must match'; END IF;
        ELSE
          SELECT * INTO previous FROM strategy_versions WHERE strategy_id = NEW.strategy_id ORDER BY revision DESC LIMIT 1;
          IF previous.id IS NULL OR NEW.revision <> previous.revision + 1
            OR ROW(NEW.owner_id,NEW.name) IS DISTINCT FROM ROW(previous.owner_id,previous.name)
            THEN RAISE EXCEPTION 'Strategy versions preserve identity and advance once'; END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER strategy_insert_guard BEFORE INSERT ON strategy_versions FOR EACH ROW EXECUTE FUNCTION guard_strategy_insert();
      CREATE FUNCTION guard_deployment_control_insert() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE previous_revision INTEGER;
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM paper_deployments WHERE id = NEW.deployment_id AND owner_id = NEW.owner_id)
          THEN RAISE EXCEPTION 'Deployment control owner must match'; END IF;
        SELECT COALESCE(MAX(revision),0) INTO previous_revision FROM deployment_control_versions WHERE deployment_id = NEW.deployment_id;
        IF NEW.revision <> previous_revision + 1 THEN RAISE EXCEPTION 'Deployment control revision must advance once'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER deployment_control_insert_guard BEFORE INSERT ON deployment_control_versions FOR EACH ROW EXECUTE FUNCTION guard_deployment_control_insert();
    """)
    for table in ("strategy_versions", "deployment_control_versions"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; strategy history is immutable.")
