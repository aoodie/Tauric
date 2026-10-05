"""Encrypted immutable model connection revisions."""

from alembic import op

revision = "0004_owner_settings"
down_revision = "0003_analysis_json_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE model_connection_versions (
          id VARCHAR(36) PRIMARY KEY,
          owner_id VARCHAR(36) NOT NULL REFERENCES owners(id),
          profile VARCHAR(20) NOT NULL CHECK (profile IN ('cloud','local')),
          revision INTEGER NOT NULL CHECK (revision > 0),
          encrypted_config TEXT NOT NULL,
          created_at TIMESTAMP WITH TIME ZONE NOT NULL,
          UNIQUE (profile, revision)
        )
    """)
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON model_connection_versions FOR EACH ROW EXECUTE FUNCTION reject_history_mutation()"
    )
    op.execute(
        "CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON model_connection_versions FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()"
    )
    op.execute("GRANT SELECT, INSERT ON model_connection_versions TO tauric_app")


def downgrade():
    raise RuntimeError("Restore a verified backup; connection history is immutable.")
