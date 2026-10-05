"""Owner chat and encrypted external channel configuration."""

from alembic import op

revision = "0010_openclaw_chat"
down_revision = "0009_scanner_candidates"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE chat_config_versions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      revision INTEGER NOT NULL UNIQUE CHECK(revision>0), encrypted_config TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE chat_turns (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      conversation_id VARCHAR(36) NOT NULL, request_id VARCHAR(36) NOT NULL,
      user_text TEXT NOT NULL, assistant_text TEXT, status VARCHAR(20) NOT NULL,
      routing_fingerprint VARCHAR(64) NOT NULL, profile VARCHAR(20) NOT NULL,
      lease_id VARCHAR(36), heartbeat_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL,
      finished_at TIMESTAMPTZ, error TEXT, usage JSON NOT NULL,
      UNIQUE(owner_id,request_id),
      CHECK(status IN ('queued','running','succeeded','failed','interrupted'))
    );
    CREATE INDEX chat_conversation ON chat_turns(owner_id,conversation_id,created_at);
    CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON chat_config_versions
      FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
    CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON chat_config_versions
      FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
    GRANT SELECT,INSERT ON chat_config_versions TO tauric_app;
    GRANT SELECT,INSERT,UPDATE ON chat_turns TO tauric_app;
    CREATE FUNCTION guard_chat_turn() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status IN ('succeeded','failed','interrupted') OR
         (NEW.id,NEW.owner_id,NEW.conversation_id,NEW.request_id,NEW.user_text,NEW.profile,
          NEW.routing_fingerprint,NEW.created_at) IS DISTINCT FROM
         (OLD.id,OLD.owner_id,OLD.conversation_id,OLD.request_id,OLD.user_text,OLD.profile,
          OLD.routing_fingerprint,OLD.created_at)
      THEN RAISE EXCEPTION 'Chat input and terminal turns are immutable'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER chat_turn_guard BEFORE UPDATE ON chat_turns
      FOR EACH ROW EXECUTE FUNCTION guard_chat_turn();
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; chat history must be preserved.")
