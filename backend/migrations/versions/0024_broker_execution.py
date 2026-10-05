"""Demo broker intents with durable correlation and immutable receipts."""

from alembic import op

revision = "0024_broker_execution"
down_revision = "0023_independent_scanners"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE broker_policy_versions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      provider VARCHAR(20) NOT NULL CHECK(provider IN ('oanda','alpaca')),
      revision INTEGER NOT NULL CHECK(revision>0), request_id VARCHAR(36) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,provider,revision), UNIQUE(owner_id,request_id)
    );
    CREATE TABLE broker_order_intents (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      provider VARCHAR(20) NOT NULL CHECK(provider IN ('oanda','alpaca')),
      request_id VARCHAR(36) NOT NULL, client_order_id VARCHAR(48) NOT NULL UNIQUE,
      policy_version_id VARCHAR(36) NOT NULL REFERENCES broker_policy_versions(id),
      connection_revision INTEGER NOT NULL CHECK(connection_revision>0),
      account_fingerprint VARCHAR(64) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
      status VARCHAR(24) NOT NULL CHECK(status IN ('prepared','submitting','pending','partially_filled','filled','cancelled','rejected','expired','unknown','cancelling')),
      created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE TABLE broker_order_events (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      intent_id VARCHAR(36) NOT NULL REFERENCES broker_order_intents(id),
      sequence INTEGER NOT NULL CHECK(sequence>0), kind VARCHAR(40) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(intent_id,sequence)
    );
    CREATE FUNCTION guard_broker_records() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous INTEGER;
    BEGIN
      IF TG_TABLE_NAME='broker_policy_versions' THEN
        SELECT COALESCE(MAX(revision),0) INTO previous FROM broker_policy_versions WHERE owner_id=NEW.owner_id AND provider=NEW.provider;
        IF NEW.revision<>previous+1 THEN RAISE EXCEPTION 'Broker policy revision must advance once'; END IF;
      ELSIF TG_TABLE_NAME='broker_order_intents' THEN
        IF TG_OP='UPDATE' AND (OLD.id<>NEW.id OR OLD.owner_id<>NEW.owner_id OR OLD.provider<>NEW.provider OR OLD.request_id<>NEW.request_id OR OLD.client_order_id<>NEW.client_order_id OR OLD.policy_version_id<>NEW.policy_version_id OR OLD.connection_revision<>NEW.connection_revision OR OLD.account_fingerprint<>NEW.account_fingerprint OR OLD.payload::text<>NEW.payload::text OR OLD.checksum<>NEW.checksum OR OLD.created_at<>NEW.created_at) THEN
          RAISE EXCEPTION 'Broker intent authority is immutable';
        END IF;
        IF TG_OP='INSERT' AND NEW.status<>'prepared' THEN
          RAISE EXCEPTION 'Broker intent must start prepared';
        END IF;
        IF TG_OP='UPDATE' AND OLD.status<>NEW.status AND NOT (
          (OLD.status='prepared' AND NEW.status IN ('submitting','cancelled','rejected')) OR
          (OLD.status='submitting' AND NEW.status IN ('pending','partially_filled','filled','cancelled','rejected','expired','unknown')) OR
          (OLD.status='pending' AND NEW.status IN ('partially_filled','filled','cancelled','rejected','expired','unknown','cancelling')) OR
          (OLD.status='partially_filled' AND NEW.status IN ('filled','cancelled','rejected','expired','unknown','cancelling')) OR
          (OLD.status='unknown' AND NEW.status IN ('pending','partially_filled','filled','cancelled','rejected','expired')) OR
          (OLD.status='cancelling' AND NEW.status IN ('pending','partially_filled','filled','cancelled','rejected','expired','unknown'))
        ) THEN RAISE EXCEPTION 'Broker intent transition is invalid'; END IF;
        IF TG_OP='UPDATE' AND OLD.status IN ('filled','cancelled','rejected','expired') AND OLD.status<>NEW.status THEN
          RAISE EXCEPTION 'Terminal broker intent cannot be reactivated';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM broker_policy_versions WHERE id=NEW.policy_version_id AND owner_id=NEW.owner_id AND provider=NEW.provider) THEN
          RAISE EXCEPTION 'Broker intent authority owner and provider must match';
        END IF;
      ELSE
        IF NOT EXISTS(SELECT 1 FROM broker_order_intents WHERE id=NEW.intent_id AND owner_id=NEW.owner_id) THEN
          RAISE EXCEPTION 'Broker receipt owner must match intent';
        END IF;
        SELECT COALESCE(MAX(sequence),0) INTO previous FROM broker_order_events WHERE intent_id=NEW.intent_id;
        IF NEW.sequence<>previous+1 THEN RAISE EXCEPTION 'Broker receipt sequence must advance once'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER broker_policy_guard BEFORE INSERT ON broker_policy_versions FOR EACH ROW EXECUTE FUNCTION guard_broker_records();
    CREATE TRIGGER broker_intent_guard BEFORE INSERT OR UPDATE ON broker_order_intents FOR EACH ROW EXECUTE FUNCTION guard_broker_records();
    CREATE TRIGGER broker_event_guard BEFORE INSERT ON broker_order_events FOR EACH ROW EXECUTE FUNCTION guard_broker_records();
    GRANT SELECT,INSERT ON broker_policy_versions,broker_order_events TO tauric_app;
    GRANT SELECT,INSERT ON broker_order_intents TO tauric_app;
    GRANT UPDATE(status,updated_at) ON broker_order_intents TO tauric_app;
    """)
    for table in ("broker_policy_versions", "broker_order_events"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
    for table in ("broker_policy_versions", "broker_order_events", "broker_order_intents"):
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
    op.execute(
        "CREATE TRIGGER immutable_delete BEFORE DELETE ON broker_order_intents FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup; broker execution history is immutable.")
