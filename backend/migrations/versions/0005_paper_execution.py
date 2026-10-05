"""Attributed immutable paper decisions, plans and fills."""

from alembic import op

revision = "0005_paper_execution"
down_revision = "0004_owner_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE paper_accounts (
          id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
          request_id VARCHAR(36) NOT NULL, name VARCHAR(80) NOT NULL,
          currency VARCHAR(12) NOT NULL CHECK (currency = 'USDT'),
          starting_balance VARCHAR(40) NOT NULL, request JSON NOT NULL,
          revision INTEGER NOT NULL CHECK (revision > 0), halted BOOLEAN NOT NULL,
          created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id, request_id)
        );
        CREATE TABLE paper_deployments (
          id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
          request_id VARCHAR(36) NOT NULL, account_id VARCHAR(36) NOT NULL REFERENCES paper_accounts(id),
          instrument_id VARCHAR(120) NOT NULL REFERENCES instruments(id),
          payload JSON NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(owner_id, request_id)
        );
        CREATE TABLE trading_records (
          id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
          account_id VARCHAR(36) NOT NULL REFERENCES paper_accounts(id),
          deployment_id VARCHAR(36) REFERENCES paper_deployments(id),
          kind VARCHAR(20) NOT NULL CHECK(kind IN ('proposal','governance','plan','fill','policy','halt')),
          request_id VARCHAR(36) NOT NULL, consumes_id VARCHAR(36) REFERENCES trading_records(id),
          payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL,
          UNIQUE(owner_id, kind, request_id), UNIQUE(kind, consumes_id)
        );
        CREATE INDEX trading_account_history ON trading_records(account_id, created_at);
    """)
    for table in ("paper_deployments", "trading_records"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO tauric_app")
    op.execute("""
        CREATE FUNCTION guard_paper_account() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'UPDATE' THEN RAISE EXCEPTION 'Paper accounts cannot be removed'; END IF;
          IF ROW(NEW.id, NEW.owner_id, NEW.request_id, NEW.name, NEW.currency,
                 NEW.starting_balance, NEW.request::jsonb, NEW.created_at)
             IS DISTINCT FROM ROW(OLD.id, OLD.owner_id, OLD.request_id, OLD.name, OLD.currency,
                 OLD.starting_balance, OLD.request::jsonb, OLD.created_at)
          THEN RAISE EXCEPTION 'Paper account identity and starting ledger are immutable'; END IF;
          IF NEW.revision <> OLD.revision + 1 THEN RAISE EXCEPTION 'Account revision must advance'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER paper_account_guard BEFORE UPDATE OR DELETE ON paper_accounts
          FOR EACH ROW EXECUTE FUNCTION guard_paper_account();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON paper_accounts
          FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        GRANT SELECT, INSERT ON paper_accounts TO tauric_app;
        GRANT UPDATE (revision, halted) ON paper_accounts TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; trading history is immutable.")
