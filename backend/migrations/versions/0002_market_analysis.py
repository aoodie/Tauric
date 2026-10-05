"""Immutable market observations and leased native analysis."""

import json

from alembic import op
from sqlalchemy import text

revision = "0002_market_analysis"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None
SCHEMA_SQL = [
    "CREATE TABLE instruments (\n"
    "\tid VARCHAR(120) NOT NULL, \n"
    "\tdefinition JSON NOT NULL, \n"
    "\tPRIMARY KEY (id)\n"
    ")",
    "CREATE TABLE worker_status (\n"
    "\tid VARCHAR(20) NOT NULL, \n"
    "\theartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tversion VARCHAR(40) NOT NULL, \n"
    "\tPRIMARY KEY (id)\n"
    ")",
    "CREATE TABLE market_snapshots (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tinstrument_id VARCHAR(120) NOT NULL, \n"
    "\tinterval VARCHAR(12) NOT NULL, \n"
    "\treceived_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tprovider_time TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tchecksum VARCHAR(64) NOT NULL, \n"
    "\tdata JSON NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(instrument_id) REFERENCES instruments (id)\n"
    ")",
    "CREATE INDEX ix_market_snapshots_instrument_id ON market_snapshots (instrument_id)",
    "CREATE TABLE analysis_jobs (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\towner_id VARCHAR(36) NOT NULL, \n"
    "\trequest_id VARCHAR(36) NOT NULL, \n"
    "\tinstrument_id VARCHAR(120) NOT NULL, \n"
    "\tsnapshot_id VARCHAR(36), \n"
    "\tprofile VARCHAR(20) NOT NULL, \n"
    "\tstatus VARCHAR(20) NOT NULL, \n"
    "\tworker_slot VARCHAR(20) NOT NULL, \n"
    "\trequest JSON NOT NULL, \n"
    "\tprogress JSON NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tstarted_at TIMESTAMP WITH TIME ZONE, \n"
    "\theartbeat_at TIMESTAMP WITH TIME ZONE, \n"
    "\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
    "\terror TEXT, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (owner_id, request_id), \n"
    "\tFOREIGN KEY(owner_id) REFERENCES owners (id), \n"
    "\tFOREIGN KEY(instrument_id) REFERENCES instruments (id), \n"
    "\tFOREIGN KEY(snapshot_id) REFERENCES market_snapshots (id)\n"
    ")",
    "CREATE TABLE analysis_artifacts (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tjob_id VARCHAR(36) NOT NULL, \n"
    "\towner_id VARCHAR(36) NOT NULL, \n"
    "\tpayload JSON NOT NULL, \n"
    "\tchecksum VARCHAR(64) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (job_id), \n"
    "\tFOREIGN KEY(job_id) REFERENCES analysis_jobs (id), \n"
    "\tFOREIGN KEY(owner_id) REFERENCES owners (id)\n"
    ")",
]
INSTRUMENTS = [
    {
        "analysis_scope": "Current daily crypto research; native sources use USD, chart uses USDT.",
        "asset_type": "crypto_spot",
        "base_currency": "BTC",
        "id": "binance:spot:BTCUSDT",
        "name": "Bitcoin",
        "native_asset_type": "crypto",
        "native_symbol": "BTC-USD",
        "provider": "binance_spot_public",
        "provider_symbol": "BTCUSDT",
        "quote_currency": "USDT",
        "symbol": "BTC/USDT",
        "volume_type": "exchange_base",
    },
    {
        "analysis_scope": "Current daily crypto research; native sources use USD, chart uses USDT.",
        "asset_type": "crypto_spot",
        "base_currency": "ETH",
        "id": "binance:spot:ETHUSDT",
        "name": "Ethereum",
        "native_asset_type": "crypto",
        "native_symbol": "ETH-USD",
        "provider": "binance_spot_public",
        "provider_symbol": "ETHUSDT",
        "quote_currency": "USDT",
        "symbol": "ETH/USDT",
        "volume_type": "exchange_base",
    },
    {
        "analysis_scope": "Current daily crypto research; native sources use USD, chart uses USDT.",
        "asset_type": "crypto_spot",
        "base_currency": "SOL",
        "id": "binance:spot:SOLUSDT",
        "name": "Solana",
        "native_asset_type": "crypto",
        "native_symbol": "SOL-USD",
        "provider": "binance_spot_public",
        "provider_symbol": "SOLUSDT",
        "quote_currency": "USDT",
        "symbol": "SOL/USDT",
        "volume_type": "exchange_base",
    },
]
SETTINGS = [
    {
        "default": 1,
        "description": "Native Tauric resource budget, captured when a run is requested.",
        "key": "analysis.max_debate_rounds",
        "kind": "integer",
        "label": "Research debate rounds",
        "maximum": 5,
        "minimum": 1,
        "options": [],
        "restart_required": False,
        "scopes": ["global"],
        "sensitive": False,
    },
    {
        "default": 1,
        "description": "Native Tauric resource budget, captured when a run is requested.",
        "key": "analysis.max_risk_discuss_rounds",
        "kind": "integer",
        "label": "Risk discussion rounds",
        "maximum": 5,
        "minimum": 1,
        "options": [],
        "restart_required": False,
        "scopes": ["global"],
        "sensitive": False,
    },
    {
        "default": 20,
        "description": "Native Tauric resource budget, captured when a run is requested.",
        "key": "analysis.max_tool_rounds",
        "kind": "integer",
        "label": "Analyst tool rounds",
        "maximum": 40,
        "minimum": 1,
        "options": [],
        "restart_required": False,
        "scopes": ["global"],
        "sensitive": False,
    },
]


def upgrade():
    bind = op.get_bind()
    for statement in SCHEMA_SQL:
        op.execute(statement)
    for spec in INSTRUMENTS:
        bind.execute(
            text(
                "INSERT INTO instruments (id, definition) VALUES (:id, CAST(:definition AS json))"
            ),
            {"id": spec["id"], "definition": json.dumps(spec)},
        )
    for spec in SETTINGS:
        bind.execute(
            text(
                "INSERT INTO setting_definitions (key, definition) VALUES (:key, CAST(:definition AS json))"
            ),
            {"key": spec["key"], "definition": json.dumps(spec)},
        )
    for table in ("instruments", "market_snapshots", "analysis_artifacts"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
    op.execute(
        "CREATE INDEX market_latest ON market_snapshots (instrument_id, interval, received_at DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX analysis_one_running ON analysis_jobs (worker_slot) WHERE status = 'running'"
    )
    op.execute(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_status CHECK (status IN ('queued','running','succeeded','failed','interrupted'))"
    )
    op.execute(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT native_worker_slot CHECK (worker_slot = 'native')"
    )
    op.execute("""
        CREATE FUNCTION protect_analysis_request() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.request IS DISTINCT FROM OLD.request
             OR NEW.id IS DISTINCT FROM OLD.id OR NEW.request_id IS DISTINCT FROM OLD.request_id
             OR NEW.instrument_id IS DISTINCT FROM OLD.instrument_id OR NEW.profile IS DISTINCT FROM OLD.profile
             OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.worker_slot IS DISTINCT FROM OLD.worker_slot THEN
            RAISE EXCEPTION 'Analysis request lineage is immutable';
          END IF;
          IF OLD.status IN ('succeeded','failed','interrupted') THEN
            RAISE EXCEPTION 'Finalized jobs are immutable';
          END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute(
        "CREATE TRIGGER immutable_request BEFORE UPDATE ON analysis_jobs FOR EACH ROW EXECUTE FUNCTION protect_analysis_request()"
    )
    op.execute(
        "CREATE TRIGGER immutable_delete BEFORE DELETE ON analysis_jobs FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
    )
    op.execute(
        "CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON analysis_jobs FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
    )
    op.execute("GRANT SELECT ON instruments TO tauric_app")
    op.execute(
        "GRANT SELECT, INSERT ON market_snapshots, analysis_artifacts, analysis_jobs TO tauric_app"
    )
    op.execute(
        "GRANT UPDATE (status, progress, started_at, heartbeat_at, finished_at, error) ON analysis_jobs TO tauric_app"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON worker_status TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; this migration holds immutable records.")
