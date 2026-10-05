"""Immutable portfolio mandates, conversation bindings and action receipts."""

from alembic import op

revision = "0016_portfolio_chat"
down_revision = "0015_autonomous_paper"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE chat_turns ADD COLUMN portfolio_id VARCHAR(36);
    CREATE INDEX ix_chat_turns_portfolio_id ON chat_turns(portfolio_id);
    CREATE TABLE portfolio_policy_versions (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL,
      anchor_version_id VARCHAR(36) NOT NULL REFERENCES capital_scope_versions(id),
      revision INTEGER NOT NULL CHECK(revision>0), request_id VARCHAR(36) NOT NULL,
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(portfolio_id,revision), UNIQUE(owner_id,request_id)
    );
    CREATE TABLE chat_portfolio_bindings (
      conversation_id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36), anchor_version_id VARCHAR(36) REFERENCES capital_scope_versions(id),
      created_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE portfolio_action_receipts (
      id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
      portfolio_id VARCHAR(36) NOT NULL, turn_id VARCHAR(36) NOT NULL REFERENCES chat_turns(id),
      request_id VARCHAR(36) NOT NULL, action VARCHAR(20) NOT NULL
        CHECK(action IN ('preferences','pause','resume')),
      payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      UNIQUE(owner_id,request_id)
    );
    CREATE FUNCTION guard_portfolio_policy() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE linked TEXT; latest_revision INTEGER;
    BEGIN
      PERFORM id FROM owners WHERE id=NEW.owner_id FOR UPDATE;
      IF NOT EXISTS(SELECT 1 FROM capital_scope_versions c WHERE c.id=NEW.anchor_version_id
        AND c.scope_id=NEW.portfolio_id AND c.owner_id=NEW.owner_id
        AND c.kind='portfolio' AND c.parent_id IS NULL)
      THEN RAISE EXCEPTION 'Portfolio must anchor an owned root allocation'; END IF;
      SELECT COALESCE(MAX(revision),0) INTO latest_revision FROM portfolio_policy_versions
        WHERE portfolio_id=NEW.portfolio_id;
      IF NEW.revision<>latest_revision+1 OR
        (NEW.payload->>'expected_revision')::INTEGER<>latest_revision OR
        NEW.payload->>'request_id'<>NEW.request_id OR
        json_typeof(NEW.payload->'allocation_ids') IS DISTINCT FROM 'array'
      THEN RAISE EXCEPTION 'Portfolio revision or request is invalid'; END IF;
      IF json_array_length(NEW.payload->'allocation_ids') NOT BETWEEN 1 AND 20 OR
        NOT (NEW.payload->'allocation_ids')::JSONB ? NEW.portfolio_id OR
        (SELECT COUNT(*) FROM json_array_elements_text(NEW.payload->'allocation_ids')) <>
        (SELECT COUNT(DISTINCT value) FROM json_array_elements_text(NEW.payload->'allocation_ids'))
      THEN RAISE EXCEPTION 'Portfolio links must be distinct and retain the root'; END IF;
      FOR linked IN SELECT json_array_elements_text(NEW.payload->'allocation_ids') LOOP
        IF NOT EXISTS(SELECT 1 FROM capital_scope_versions c WHERE c.scope_id=linked
          AND c.owner_id=NEW.owner_id AND c.kind='portfolio' AND c.parent_id IS NULL)
          OR EXISTS(SELECT 1 FROM (SELECT DISTINCT ON (portfolio_id) *
            FROM portfolio_policy_versions WHERE owner_id=NEW.owner_id
            ORDER BY portfolio_id,revision DESC) p WHERE p.portfolio_id<>NEW.portfolio_id
            AND (p.payload->'allocation_ids')::JSONB ? linked)
          OR (linked<>NEW.portfolio_id AND (
            EXISTS(SELECT 1 FROM portfolio_policy_versions WHERE portfolio_id=linked)
            OR EXISTS(SELECT 1 FROM chat_portfolio_bindings WHERE portfolio_id=linked)))
        THEN RAISE EXCEPTION 'Portfolio allocation is unavailable or already bound'; END IF;
      END LOOP;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_policy_guard BEFORE INSERT ON portfolio_policy_versions
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_policy();
    CREATE FUNCTION guard_portfolio_binding() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM id FROM owners WHERE id=NEW.owner_id FOR UPDATE;
      IF (NEW.portfolio_id IS NULL)<>(NEW.anchor_version_id IS NULL) OR
        (NEW.portfolio_id IS NOT NULL AND (NOT EXISTS(SELECT 1 FROM capital_scope_versions c
          WHERE c.id=NEW.anchor_version_id AND c.scope_id=NEW.portfolio_id
          AND c.owner_id=NEW.owner_id AND c.kind='portfolio' AND c.parent_id IS NULL)
          OR EXISTS(SELECT 1 FROM (SELECT DISTINCT ON (portfolio_id) *
            FROM portfolio_policy_versions WHERE owner_id=NEW.owner_id
            ORDER BY portfolio_id,revision DESC) p WHERE p.portfolio_id<>NEW.portfolio_id
            AND (p.payload->'allocation_ids')::JSONB ? NEW.portfolio_id)))
        OR EXISTS(SELECT 1 FROM chat_turns t WHERE t.conversation_id=NEW.conversation_id
          AND (t.owner_id<>NEW.owner_id OR t.portfolio_id IS DISTINCT FROM NEW.portfolio_id))
      THEN RAISE EXCEPTION 'Conversation context cannot be reassigned'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_binding_guard BEFORE INSERT ON chat_portfolio_bindings
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_binding();
    CREATE FUNCTION guard_chat_portfolio() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='UPDATE' AND NEW.portfolio_id IS DISTINCT FROM OLD.portfolio_id
      THEN RAISE EXCEPTION 'Chat portfolio is immutable'; END IF;
      IF (NEW.portfolio_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM chat_portfolio_bindings b
        WHERE b.conversation_id=NEW.conversation_id AND b.owner_id=NEW.owner_id
          AND b.portfolio_id=NEW.portfolio_id)) OR EXISTS(SELECT 1 FROM chat_portfolio_bindings b
        WHERE b.conversation_id=NEW.conversation_id AND (b.owner_id<>NEW.owner_id
          OR b.portfolio_id IS DISTINCT FROM NEW.portfolio_id))
      THEN RAISE EXCEPTION 'Chat must retain its bound owner and portfolio'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER chat_portfolio_guard BEFORE INSERT OR UPDATE ON chat_turns
      FOR EACH ROW EXECUTE FUNCTION guard_chat_portfolio();
    CREATE FUNCTION guard_portfolio_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM chat_turns t WHERE t.id=NEW.turn_id
        AND t.owner_id=NEW.owner_id AND t.portfolio_id=NEW.portfolio_id AND t.status='running')
      THEN RAISE EXCEPTION 'Receipt must reference its active owned portfolio turn'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER portfolio_receipt_guard BEFORE INSERT ON portfolio_action_receipts
      FOR EACH ROW EXECUTE FUNCTION guard_portfolio_receipt();
    """)
    for table in (
        "portfolio_policy_versions",
        "chat_portfolio_bindings",
        "portfolio_action_receipts",
    ):
        op.execute(f"""
        CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table}
          FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        GRANT SELECT,INSERT ON {table} TO tauric_app;
        """)


def downgrade():
    raise RuntimeError("Restore a verified backup; portfolio and chat history is immutable.")
