"""Immutable retrospective datasets and deterministic research results."""

from alembic import op

revision = "0012_research_analytics"
down_revision = "0011_chat_research"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE research_records (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      kind VARCHAR(20) NOT NULL CHECK(kind IN ('dataset','run')),
      request_id VARCHAR(36) NOT NULL, parent_id VARCHAR(36) REFERENCES research_records(id),
      name VARCHAR(120) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,kind,request_id),
      CHECK((kind='dataset' AND parent_id IS NULL) OR (kind='run' AND parent_id IS NOT NULL))
    );
    CREATE INDEX research_owner_time ON research_records(owner_id,kind,created_at);
    CREATE FUNCTION guard_research_record() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.kind='run' AND NOT EXISTS(SELECT 1 FROM research_records r
        WHERE r.id=NEW.parent_id AND r.kind='dataset' AND r.owner_id=NEW.owner_id)
      THEN RAISE EXCEPTION 'Research run must reference its owner dataset'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER research_owner_guard BEFORE INSERT ON research_records FOR EACH ROW EXECUTE FUNCTION guard_research_record();
    CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON research_records FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON research_records FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
    GRANT SELECT,INSERT ON research_records TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; research history is immutable.")
