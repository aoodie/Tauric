"""Immutable paper portfolios and nested allocation versions; history stays intact."""

from alembic import op

revision = "0007_capital_scopes"
down_revision = "0006_governance_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE capital_scope_versions (
        id VARCHAR(36) PRIMARY KEY, scope_id VARCHAR(36) NOT NULL,
        owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        account_id VARCHAR(36) NOT NULL REFERENCES paper_accounts(id),
        parent_id VARCHAR(36) REFERENCES capital_scope_versions(id),
        kind VARCHAR(20) NOT NULL CHECK(kind IN ('portfolio','pot')),
        name VARCHAR(80) NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
        request_id VARCHAR(36) NOT NULL, payload JSON NOT NULL,
        checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(scope_id, revision), UNIQUE(owner_id, request_id),
        CHECK((kind = 'portfolio') = (parent_id IS NULL))
      );
      CREATE INDEX capital_account_history ON capital_scope_versions(account_id, scope_id, revision);
      CREATE FUNCTION guard_capital_insert() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE previous capital_scope_versions; parent capital_scope_versions;
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM paper_accounts WHERE id = NEW.account_id AND owner_id = NEW.owner_id)
          THEN RAISE EXCEPTION 'Capital owner and account must match'; END IF;
        IF NEW.revision = 1 THEN
          IF NEW.scope_id <> NEW.id THEN RAISE EXCEPTION 'Initial capital identity must match'; END IF;
        ELSE
          SELECT * INTO previous FROM capital_scope_versions
            WHERE scope_id = NEW.scope_id ORDER BY revision DESC LIMIT 1;
          IF previous.id IS NULL OR NEW.revision <> previous.revision + 1 OR
            ROW(NEW.owner_id,NEW.account_id,NEW.parent_id,NEW.kind,NEW.name)
            IS DISTINCT FROM ROW(previous.owner_id,previous.account_id,previous.parent_id,previous.kind,previous.name)
            THEN RAISE EXCEPTION 'Capital versions must preserve identity and advance once'; END IF;
        END IF;
        IF NEW.parent_id IS NOT NULL THEN
          SELECT * INTO parent FROM capital_scope_versions WHERE id = NEW.parent_id;
          IF parent.id IS NULL OR parent.revision <> 1 OR parent.account_id <> NEW.account_id
            OR parent.owner_id <> NEW.owner_id OR parent.scope_id = NEW.scope_id
            THEN RAISE EXCEPTION 'Capital parent must be an existing identity in the same account'; END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER capital_insert_guard BEFORE INSERT ON capital_scope_versions
        FOR EACH ROW EXECUTE FUNCTION guard_capital_insert();
      CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON capital_scope_versions
        FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
      CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON capital_scope_versions
        FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
      GRANT SELECT, INSERT ON capital_scope_versions TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; capital history is immutable.")
