"""Immutable receipts for bounded owner management and upgrade drafts."""

from alembic import op

revision = "0030_assistant_management"
down_revision = "0029_market_intelligence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE assistant_records (
            id VARCHAR(36) PRIMARY KEY,
            owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
            turn_id VARCHAR(36) REFERENCES chat_turns(id),
            request_id VARCHAR(36) NOT NULL,
            stage VARCHAR(20) NOT NULL CHECK(stage IN ('requested','completed')),
            operation VARCHAR(30) NOT NULL,
            title VARCHAR(120) NOT NULL,
            payload JSON NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE(owner_id,request_id,stage)
        );
        CREATE INDEX assistant_owner_history ON assistant_records(owner_id,created_at DESC);
        CREATE INDEX assistant_turn_history ON assistant_records(turn_id,created_at);
        CREATE FUNCTION guard_assistant_record() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.turn_id IS NOT NULL AND NOT EXISTS(
                SELECT 1 FROM chat_turns t WHERE t.id=NEW.turn_id
                  AND t.owner_id=NEW.owner_id AND t.portfolio_id IS NULL)
            THEN RAISE EXCEPTION 'Project management requires its owner general conversation'; END IF;
            IF NEW.stage='completed' AND NOT EXISTS(
                SELECT 1 FROM assistant_records i WHERE i.owner_id=NEW.owner_id
                  AND i.request_id=NEW.request_id AND i.stage='requested'
                  AND i.operation=NEW.operation
                  AND (i.payload->'inputs')::jsonb=(NEW.payload->'inputs')::jsonb)
            THEN RAISE EXCEPTION 'Assistant completion must match its immutable request'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER assistant_owner_guard BEFORE INSERT ON assistant_records
            FOR EACH ROW EXECUTE FUNCTION guard_assistant_record();
        CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON assistant_records
            FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON assistant_records
            FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        GRANT SELECT,INSERT ON assistant_records TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; assistant action history is immutable.")
