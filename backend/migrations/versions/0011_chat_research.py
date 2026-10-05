"""Immutable chat research admissions across owner channels."""

from alembic import op

revision = "0011_chat_research"
down_revision = "0010_openclaw_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE chat_research (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      turn_id VARCHAR(36) REFERENCES chat_turns(id), job_id VARCHAR(36) NOT NULL REFERENCES analysis_jobs(id),
      channel VARCHAR(20) NOT NULL CHECK(channel IN ('webchat','telegram','discord')),
      request_key VARCHAR(64) NOT NULL, research_key VARCHAR(64) NOT NULL,
      charged BOOLEAN NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,request_key)
    );
    CREATE INDEX chat_research_key ON chat_research(owner_id,research_key);
    CREATE INDEX chat_research_turn ON chat_research(turn_id);
    CREATE FUNCTION guard_chat_research() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM analysis_jobs j WHERE j.id=NEW.job_id AND j.owner_id=NEW.owner_id)
        OR (NEW.turn_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM chat_turns t WHERE t.id=NEW.turn_id AND t.owner_id=NEW.owner_id))
      THEN RAISE EXCEPTION 'Chat research owner must match its references'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER chat_research_guard BEFORE INSERT ON chat_research FOR EACH ROW EXECUTE FUNCTION guard_chat_research();
    CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON chat_research FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON chat_research FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
    GRANT SELECT,INSERT ON chat_research TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; chat research history is immutable.")
