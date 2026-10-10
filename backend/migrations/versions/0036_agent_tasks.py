"""Durable project work, wake lineage and isolated engineering receipts."""

from alembic import op

revision = "0036_agent_tasks"
down_revision = "0035_scanner_budget"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE agent_tasks (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        request_id VARCHAR(36) NOT NULL, parent_id VARCHAR(36) REFERENCES agent_tasks(id),
        root_id VARCHAR(36) NOT NULL, conversation_id VARCHAR(36) NOT NULL,
        objective TEXT NOT NULL, criteria JSON NOT NULL, dependencies JSON NOT NULL,
        scope JSON NOT NULL, status VARCHAR(24) NOT NULL, revision INTEGER NOT NULL,
        max_wakes INTEGER NOT NULL, max_attempts INTEGER NOT NULL, attempts INTEGER NOT NULL,
        deadline TIMESTAMPTZ NOT NULL, next_check_at TIMESTAMPTZ,
        waiting JSON NOT NULL, result JSON NOT NULL, error TEXT,
        created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE(owner_id,request_id)
      );
      CREATE INDEX agent_task_due ON agent_tasks(status,next_check_at);
      CREATE TABLE agent_task_wakes (
        turn_id VARCHAR(36) PRIMARY KEY REFERENCES chat_turns(id),
        task_id VARCHAR(36) NOT NULL REFERENCES agent_tasks(id),
        task_revision INTEGER NOT NULL, processed BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL
      );
      CREATE TABLE agent_task_events (
        id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36) NOT NULL REFERENCES agent_tasks(id),
        owner_id VARCHAR(36) NOT NULL REFERENCES owners(id), sequence INTEGER NOT NULL,
        kind VARCHAR(40) NOT NULL, payload JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, UNIQUE(task_id,sequence)
      );
      CREATE TABLE engineering_jobs (
        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
        task_id VARCHAR(36) NOT NULL REFERENCES agent_tasks(id),
        request_key VARCHAR(64) NOT NULL, inputs JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
        status VARCHAR(24) NOT NULL, result JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
        UNIQUE(task_id,request_key)
      );
      CREATE OR REPLACE FUNCTION immutable_agent_task_event() RETURNS trigger AS $$
      BEGIN RAISE EXCEPTION 'Agent task events are immutable'; END;
      $$ LANGUAGE plpgsql;
      CREATE TRIGGER immutable_agent_task_events BEFORE UPDATE OR DELETE ON agent_task_events
        FOR EACH ROW EXECUTE FUNCTION immutable_agent_task_event();
      GRANT SELECT,INSERT,UPDATE ON agent_tasks,agent_task_wakes,engineering_jobs TO tauric_app;
      GRANT SELECT,INSERT ON agent_task_events TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup before changing the running release schema.")
