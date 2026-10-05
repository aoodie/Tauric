"""Owner, settings, layouts and database-enforced append-only history."""

import json

from alembic import op
from sqlalchemy import text

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


# Frozen schema and definitions: future model changes must use a new migration.
SCHEMA_SQL = [
    "CREATE TABLE login_attempts (\n"
    "\tkey VARCHAR(64) NOT NULL, \n"
    "\tattempts INTEGER NOT NULL, \n"
    "\twindow_start TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (key)\n"
    ")",
    "CREATE TABLE owners (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tsingleton BOOLEAN NOT NULL, \n"
    "\tusername VARCHAR(80) NOT NULL, \n"
    "\tpassword_hash TEXT NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (singleton), \n"
    "\tUNIQUE (username)\n"
    ")",
    "CREATE TABLE setting_definitions (\n"
    "\tkey VARCHAR(120) NOT NULL, \n"
    "\tdefinition JSON NOT NULL, \n"
    "\tPRIMARY KEY (key)\n"
    ")",
    "CREATE TABLE audit_events (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\towner_id VARCHAR(36) NOT NULL, \n"
    "\tactor VARCHAR(80) NOT NULL, \n"
    "\taction VARCHAR(80) NOT NULL, \n"
    "\tresource_type VARCHAR(40) NOT NULL, \n"
    "\tresource_id VARCHAR(120) NOT NULL, \n"
    "\treason VARCHAR(500) NOT NULL, \n"
    "\tdetails JSON NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(owner_id) REFERENCES owners (id)\n"
    ")",
    "CREATE INDEX ix_audit_events_created_at ON audit_events (created_at)",
    "CREATE TABLE owner_sessions (\n"
    "\ttoken_hash VARCHAR(64) NOT NULL, \n"
    "\towner_id VARCHAR(36) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\trevoked_at TIMESTAMP WITH TIME ZONE, \n"
    "\tPRIMARY KEY (token_hash), \n"
    "\tFOREIGN KEY(owner_id) REFERENCES owners (id)\n"
    ")",
    "CREATE TABLE setting_heads (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\towner_id VARCHAR(36) NOT NULL, \n"
    "\tkey VARCHAR(120) NOT NULL, \n"
    "\tscope VARCHAR(20) NOT NULL, \n"
    "\trevision INTEGER NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (owner_id, key, scope), \n"
    "\tFOREIGN KEY(owner_id) REFERENCES owners (id), \n"
    "\tFOREIGN KEY(key) REFERENCES setting_definitions (key)\n"
    ")",
    "CREATE TABLE workspaces (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\towner_id VARCHAR(36) NOT NULL, \n"
    "\tname VARCHAR(80) NOT NULL, \n"
    "\trevision INTEGER NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tFOREIGN KEY(owner_id) REFERENCES owners (id)\n"
    ")",
    "CREATE TABLE layout_versions (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\tworkspace_id VARCHAR(36) NOT NULL, \n"
    "\trevision INTEGER NOT NULL, \n"
    "\tlayout JSON NOT NULL, \n"
    "\tactor VARCHAR(36) NOT NULL, \n"
    "\treason VARCHAR(500) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (workspace_id, revision), \n"
    "\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id), \n"
    "\tFOREIGN KEY(actor) REFERENCES owners (id)\n"
    ")",
    "CREATE TABLE setting_versions (\n"
    "\tid VARCHAR(36) NOT NULL, \n"
    "\thead_id VARCHAR(36) NOT NULL, \n"
    "\trevision INTEGER NOT NULL, \n"
    "\tvalue JSON, \n"
    "\tinherited BOOLEAN NOT NULL, \n"
    "\tactor VARCHAR(36) NOT NULL, \n"
    "\treason VARCHAR(500) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tPRIMARY KEY (id), \n"
    "\tUNIQUE (head_id, revision), \n"
    "\tFOREIGN KEY(head_id) REFERENCES setting_heads (id), \n"
    "\tFOREIGN KEY(actor) REFERENCES owners (id)\n"
    ")",
]
DEFINITIONS = [
    {
        "default": "compact",
        "description": "Spacing and type size throughout the workstation.",
        "key": "ui.density",
        "kind": "enum",
        "label": "Interface density",
        "maximum": None,
        "minimum": None,
        "options": ["comfortable", "compact", "terminal"],
        "restart_required": False,
        "scopes": ["global", "user"],
        "sensitive": False,
    },
    {
        "default": "Europe/London",
        "description": "Times are stored in UTC and displayed in this timezone.",
        "key": "ui.timezone",
        "kind": "timezone",
        "label": "Display timezone",
        "maximum": None,
        "minimum": None,
        "options": [],
        "restart_required": False,
        "scopes": ["global", "user"],
        "sensitive": False,
    },
    {
        "default": True,
        "description": "Confirm before switching or resetting a workspace with unsaved changes.",
        "key": "workspace.confirm_unsaved",
        "kind": "boolean",
        "label": "Protect unsaved layouts",
        "maximum": None,
        "minimum": None,
        "options": [],
        "restart_required": False,
        "scopes": ["global", "user"],
        "sensitive": False,
    },
    {
        "default": 1,
        "description": "Maximum simultaneous expensive jobs when workers are added. No workers are "
        "running in this milestone.",
        "key": "system.worker_concurrency",
        "kind": "integer",
        "label": "Analysis concurrency",
        "maximum": 8,
        "minimum": 1,
        "options": [],
        "restart_required": True,
        "scopes": ["global"],
        "sensitive": False,
    },
]


def upgrade():
    bind = op.get_bind()
    for statement in SCHEMA_SQL:
        op.execute(statement)
    op.execute("ALTER TABLE owners ADD CONSTRAINT owner_singleton_true CHECK (singleton = true)")
    for spec in DEFINITIONS:
        bind.execute(
            text(
                "INSERT INTO setting_definitions (key, definition) VALUES (:key, CAST(:definition AS json))"
            ),
            {"key": spec["key"], "definition": json.dumps(spec)},
        )
    op.execute("""
        CREATE FUNCTION reject_history_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Historical records are immutable';
        END; $$
    """)
    for table in ("audit_events", "setting_versions", "layout_versions", "setting_definitions"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
        )
    # Runtime role is created by the database initialization step, never by API code.
    op.execute("GRANT USAGE ON SCHEMA public TO tauric_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON owners, owner_sessions, login_attempts, setting_heads, workspaces TO tauric_app"
    )
    op.execute(
        "GRANT SELECT, INSERT ON audit_events, setting_versions, layout_versions TO tauric_app"
    )
    op.execute("GRANT SELECT ON setting_definitions, alembic_version TO tauric_app")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def downgrade():
    raise RuntimeError(
        "This migration contains immutable history. Restore a verified backup instead of dropping it."
    )
