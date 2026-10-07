"""Private owner knowledge and optional local memory synchronization."""

from alembic import op

revision = "0031_knowledge_memory"
down_revision = "0030_assistant_management"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE knowledge_documents (
            id VARCHAR(36) PRIMARY KEY,
            owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
            source_kind VARCHAR(20) NOT NULL,
            source_id VARCHAR(250) NOT NULL,
            kind VARCHAR(20) NOT NULL CHECK(kind IN ('strategy','chat','research','preference','wiki')),
            title VARCHAR(160) NOT NULL,
            body TEXT NOT NULL,
            provenance JSON NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            revision INTEGER NOT NULL CHECK(revision>0),
            excluded BOOLEAN NOT NULL,
            export_hash VARCHAR(64),
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE(owner_id,source_kind,source_id)
        );
        CREATE INDEX knowledge_owner ON knowledge_documents(owner_id,kind,updated_at DESC);
        CREATE INDEX knowledge_search ON knowledge_documents USING GIN
            (to_tsvector('english',title || ' ' || body));
        CREATE TABLE memory_settings_versions (
            id VARCHAR(36) PRIMARY KEY,
            owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
            revision INTEGER NOT NULL,
            payload JSON NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE(owner_id,revision)
        );
        CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON memory_settings_versions
            FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON memory_settings_versions
            FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation();
        CREATE TABLE memory_sync_state (
            owner_id VARCHAR(36) PRIMARY KEY REFERENCES owners(id),
            heartbeat_at TIMESTAMPTZ NOT NULL,
            last_collected_at TIMESTAMPTZ,
            last_honcho_at TIMESTAMPTZ,
            last_error VARCHAR(250),
            representation TEXT NOT NULL,
            honcho_selection_hash VARCHAR(64),
            honcho_workspace VARCHAR(120),
            honcho_retired_workspaces JSON NOT NULL,
            vault_conflicts INTEGER NOT NULL
        );
        CREATE TABLE honcho_deliveries (
            document_id VARCHAR(36) PRIMARY KEY REFERENCES knowledge_documents(id),
            checksum VARCHAR(64) NOT NULL,
            status VARCHAR(20) NOT NULL CHECK(status IN ('pending','sending','sent','uncertain')),
            remote_ids JSON NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        );
        GRANT SELECT,INSERT ON memory_settings_versions TO tauric_app;
        GRANT SELECT,INSERT,UPDATE,DELETE ON knowledge_documents,memory_sync_state,honcho_deliveries TO tauric_app;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; memory setting history is immutable.")
