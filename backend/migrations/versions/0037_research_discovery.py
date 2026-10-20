"""Persistent compressed history and research discovery, isolated from financial authority."""

from alembic import op

revision = "0037_research_discovery"
down_revision = "0036_agent_tasks"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE research_history_chunks (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        instrument_id VARCHAR(120) NOT NULL, interval VARCHAR(8) NOT NULL,
        start_at TIMESTAMPTZ NOT NULL, end_at TIMESTAMPTZ NOT NULL,
        bars INTEGER NOT NULL, content BYTEA NOT NULL, checksum VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, CHECK(end_at > start_at),
        CHECK(bars BETWEEN 0 AND 5000), CHECK(octet_length(content) <= 4000000)
      );
      CREATE INDEX research_history_range ON research_history_chunks(owner_id,instrument_id,interval,start_at,end_at);
      CREATE TABLE research_discovery_records (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        kind VARCHAR(24) NOT NULL, request_id VARCHAR(36) NOT NULL,
        parent_id VARCHAR(36) REFERENCES research_discovery_records(id),
        payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(owner_id,kind,request_id),
        CHECK(kind IN ('settings','history_sync','campaign','trial','confirmation','ai_usage','holdout','event'))
      );
      CREATE INDEX research_discovery_owner_kind ON research_discovery_records(owner_id,kind,created_at);
      CREATE FUNCTION guard_research_discovery() RETURNS trigger AS $$
      BEGIN
        IF TG_OP <> 'UPDATE' OR OLD.kind NOT IN ('history_sync','campaign','trial','confirmation','ai_usage') THEN
          RAISE EXCEPTION 'Research evidence is immutable';
        END IF;
        IF ROW(OLD.id,OLD.owner_id,OLD.kind,OLD.request_id,OLD.parent_id,OLD.created_at)
           IS DISTINCT FROM ROW(NEW.id,NEW.owner_id,NEW.kind,NEW.request_id,NEW.parent_id,NEW.created_at)
           OR OLD.payload::jsonb->'request' IS DISTINCT FROM NEW.payload::jsonb->'request'
           OR OLD.payload::jsonb->'input' IS DISTINCT FROM NEW.payload::jsonb->'input' THEN
          RAISE EXCEPTION 'Frozen research inputs cannot change';
        END IF;
        IF OLD.payload->>'status' IN ('completed','failed','cancelled','rejected','screen_passed') THEN
          RAISE EXCEPTION 'Terminal research evidence is immutable';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER research_discovery_guard BEFORE UPDATE OR DELETE ON research_discovery_records
        FOR EACH ROW EXECUTE FUNCTION guard_research_discovery();
      CREATE FUNCTION immutable_research_history() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'Saved research history is immutable'; END; $$ LANGUAGE plpgsql;
      CREATE TRIGGER research_history_immutable BEFORE UPDATE OR DELETE ON research_history_chunks
        FOR EACH ROW EXECUTE FUNCTION immutable_research_history();
      CREATE TRIGGER research_history_no_truncate BEFORE TRUNCATE ON research_history_chunks
        EXECUTE FUNCTION immutable_research_history();
      CREATE TRIGGER research_discovery_no_truncate BEFORE TRUNCATE ON research_discovery_records
        EXECUTE FUNCTION immutable_research_history();
      GRANT SELECT,INSERT ON research_history_chunks TO tauric_app;
      GRANT SELECT,INSERT,UPDATE ON research_discovery_records TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup before changing research history schema")
