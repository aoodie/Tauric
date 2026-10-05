"""Track broker-held protection, attributed outcomes and durable close actions."""

from alembic import op

revision = "0025_broker_positions"
down_revision = "0024_broker_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE broker_position_cursors (
      entry_intent_id VARCHAR(36) PRIMARY KEY REFERENCES broker_order_intents(id),
      owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      status VARCHAR(24) NOT NULL CHECK(status IN ('unfilled','open','partially_closed','closed','unknown')),
      updated_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE broker_close_requests (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      request_id VARCHAR(36) NOT NULL, entry_intent_id VARCHAR(36) NOT NULL REFERENCES broker_order_intents(id),
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      status VARCHAR(24) NOT NULL CHECK(status IN ('prepared','cancelling_protection','submitting','pending','filled','rejected','unknown')),
      created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE UNIQUE INDEX broker_one_unresolved_close ON broker_close_requests(entry_intent_id)
      WHERE status NOT IN ('filled','rejected');
    CREATE FUNCTION guard_broker_position_records() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM broker_order_intents WHERE id=NEW.entry_intent_id AND owner_id=NEW.owner_id
        AND payload->'order'->>'position_effect'='open') THEN RAISE EXCEPTION 'Broker position must reference an owned entry'; END IF;
      IF TG_TABLE_NAME='broker_position_cursors' THEN
        IF TG_OP='UPDATE' AND (OLD.entry_intent_id<>NEW.entry_intent_id OR OLD.owner_id<>NEW.owner_id) THEN RAISE EXCEPTION 'Position cursor identity is immutable'; END IF;
        IF TG_OP='UPDATE' AND OLD.status='closed' AND NEW.status<>'closed' THEN RAISE EXCEPTION 'Closed position cannot be reactivated'; END IF;
      ELSE
        IF TG_OP='INSERT' AND NEW.status<>'prepared' THEN RAISE EXCEPTION 'Close request must start prepared'; END IF;
        IF TG_OP='UPDATE' AND (OLD.id<>NEW.id OR OLD.owner_id<>NEW.owner_id OR OLD.request_id<>NEW.request_id OR OLD.entry_intent_id<>NEW.entry_intent_id OR OLD.payload::text<>NEW.payload::text OR OLD.checksum<>NEW.checksum OR OLD.created_at<>NEW.created_at) THEN RAISE EXCEPTION 'Broker close authority is immutable'; END IF;
        IF TG_OP='UPDATE' AND NEW.status IN ('submitting','cancelling_protection') AND OLD.status<>NEW.status
          AND EXISTS(SELECT 1 FROM broker_order_events WHERE intent_id=NEW.entry_intent_id
            AND kind='close_submission_started' AND payload->>'close_request_id'=NEW.id)
          THEN RAISE EXCEPTION 'An issued broker close cannot be submitted again'; END IF;
        IF TG_OP='UPDATE' AND OLD.status<>NEW.status AND NOT (
          (OLD.status='prepared' AND NEW.status IN ('cancelling_protection','submitting','filled','rejected','unknown')) OR
          (OLD.status='cancelling_protection' AND NEW.status IN ('submitting','filled','rejected','unknown')) OR
          (OLD.status='submitting' AND NEW.status IN ('pending','filled','rejected','unknown')) OR
          (OLD.status='pending' AND NEW.status IN ('filled','rejected','unknown')) OR
          (OLD.status='unknown' AND NEW.status IN ('cancelling_protection','submitting','pending','filled','rejected'))
        ) THEN RAISE EXCEPTION 'Broker close transition is invalid'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER broker_position_guard BEFORE INSERT OR UPDATE ON broker_position_cursors FOR EACH ROW EXECUTE FUNCTION guard_broker_position_records();
    CREATE TRIGGER broker_close_guard BEFORE INSERT OR UPDATE ON broker_close_requests FOR EACH ROW EXECUTE FUNCTION guard_broker_position_records();
    GRANT SELECT,INSERT ON broker_position_cursors,broker_close_requests TO tauric_app;
    GRANT UPDATE(status,updated_at) ON broker_position_cursors,broker_close_requests TO tauric_app;
    """)
    for table in ("broker_position_cursors", "broker_close_requests"):
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )


def downgrade():
    raise RuntimeError("Restore a verified backup; broker position records are immutable.")
