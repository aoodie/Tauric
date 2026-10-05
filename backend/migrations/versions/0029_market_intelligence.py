"""Persist sourced market intelligence and versioned personal watchlists."""

from alembic import op

revision = "0029_market_intelligence"
down_revision = "0028_broker_routes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE intelligence_receipts (
            id VARCHAR(36) PRIMARY KEY,
            owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
            source_key VARCHAR(120) NOT NULL, payload JSON NOT NULL,
            checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX intelligence_source_history ON intelligence_receipts(owner_id,source_key,created_at DESC);
        CREATE TABLE intelligence_watchlists (
            id VARCHAR(36) PRIMARY KEY,
            owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
            revision INT NOT NULL CHECK(revision>0), instrument_ids JSON NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id,revision),
            CHECK(json_typeof(instrument_ids)='array' AND json_array_length(instrument_ids)<=200)
        );
        CREATE FUNCTION guard_intelligence_watchlist() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM intelligence_watchlists WHERE owner_id=NEW.owner_id)
              OR EXISTS(SELECT 1 FROM json_array_elements_text(NEW.instrument_ids) AS x(id)
                WHERE NOT EXISTS(SELECT 1 FROM instruments i WHERE i.id=x.id))
              THEN RAISE EXCEPTION 'Invalid watchlist revision or instrument'; END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER intelligence_watchlist_guard BEFORE INSERT ON intelligence_watchlists
            FOR EACH ROW EXECUTE FUNCTION guard_intelligence_watchlist();
    """)
    for table in ("intelligence_receipts", "intelligence_watchlists"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT,INSERT ON {table} TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; intelligence history is immutable.")
