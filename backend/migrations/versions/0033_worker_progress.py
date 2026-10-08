"""Separate worker liveness from successful work and sanitized failures."""

from alembic import op

revision = "0033_worker_progress"
down_revision = "0032_live_readiness"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE worker_lane_progress (
            worker VARCHAR(24) NOT NULL, lane VARCHAR(40) NOT NULL,
            attempts BIGINT NOT NULL DEFAULT 0, successes BIGINT NOT NULL DEFAULT 0,
            failures BIGINT NOT NULL DEFAULT 0, work_completed BIGINT NOT NULL DEFAULT 0,
            consecutive_failures BIGINT NOT NULL DEFAULT 0,
            invocation_id VARCHAR(36) NOT NULL, started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ, last_success_at TIMESTAMPTZ,
            last_work_at TIMESTAMPTZ, last_failure_at TIMESTAMPTZ,
            error_category VARCHAR(32), duration_ms BIGINT,
            PRIMARY KEY(worker,lane),
            CHECK(attempts>=0 AND successes>=0 AND failures>=0 AND work_completed>=0
                AND consecutive_failures>=0 AND (duration_ms IS NULL OR duration_ms>=0))
        );
        GRANT SELECT,INSERT,UPDATE ON worker_lane_progress TO tauric_app;
    """)


def downgrade():
    op.execute("DROP TABLE worker_lane_progress")
