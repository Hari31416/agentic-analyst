"""Personal thread, message, and artifact pins."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e42c851ab901"
down_revision = "b19d4a2e730f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pins",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.String(36),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id", "kind", "target_id", name="uq_pins_owner_target"
        ),
        sa.CheckConstraint(
            "(kind = 'thread' AND target_id = thread_id AND message_id IS NULL AND artifact_id IS NULL) OR "
            "(kind = 'message' AND target_id = message_id AND message_id IS NOT NULL AND artifact_id IS NULL) OR "
            "(kind = 'artifact' AND target_id = artifact_id AND artifact_id IS NOT NULL AND message_id IS NULL)",
            name="ck_pins_target",
        ),
    )
    op.create_index("ix_pins_user_id", "pins", ["user_id"])
    op.create_index("ix_pins_thread_id", "pins", ["thread_id"])


def downgrade():
    op.drop_table("pins")
