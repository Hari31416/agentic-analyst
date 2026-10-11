"""Transactional wakeup hints for durable jobs and run events."""

from alembic import op

revision = "ba321c7d801a"
down_revision = "a74e013cd215"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION analyst_notify_job() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.state = 'queued' THEN
                PERFORM pg_notify('analyst_jobs', '');
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER analyst_job_wakeup AFTER INSERT OR UPDATE OF state, available_at
        ON jobs FOR EACH ROW EXECUTE FUNCTION analyst_notify_job()
    """)
    op.execute("""
        CREATE FUNCTION analyst_notify_run_event() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('analyst_run_events', NEW.run_id::text);
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER analyst_run_event_wakeup AFTER INSERT
        ON events FOR EACH ROW EXECUTE FUNCTION analyst_notify_run_event()
    """)


def downgrade():
    op.execute("DROP TRIGGER analyst_run_event_wakeup ON events")
    op.execute("DROP FUNCTION analyst_notify_run_event()")
    op.execute("DROP TRIGGER analyst_job_wakeup ON jobs")
    op.execute("DROP FUNCTION analyst_notify_job()")
