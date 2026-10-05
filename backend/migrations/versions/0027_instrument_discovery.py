"""Permit additive provider-verified research instrument registration."""

from alembic import op

revision = "0027_instrument_discovery"
down_revision = "0026_broker_review"
branch_labels = None
depends_on = None


def upgrade():
    # Existing immutable-record triggers still reject updates/deletes/truncation.
    op.execute("GRANT INSERT ON instruments TO tauric_app")


def downgrade():
    op.execute("REVOKE INSERT ON instruments FROM tauric_app")
