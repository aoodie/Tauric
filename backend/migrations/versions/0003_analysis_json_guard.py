"""Compare JSON request lineage through PostgreSQL's comparable JSONB type."""

from alembic import op

revision = "0003_analysis_json_guard"
down_revision = "0002_market_analysis"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE OR REPLACE FUNCTION protect_analysis_request() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.owner_id IS DISTINCT FROM OLD.owner_id OR NEW.request::jsonb IS DISTINCT FROM OLD.request::jsonb
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


def downgrade():
    raise RuntimeError("Restore a verified backup; this migration protects immutable records.")
