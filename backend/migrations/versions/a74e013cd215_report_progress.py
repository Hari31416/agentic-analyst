"""Persist report generation progress for reconnecting clients."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a74e013cd215"
down_revision = "f63d902bc104"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_versions",
        sa.Column(
            "progress",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("report_versions", "progress", server_default=None)


def downgrade():
    op.drop_column("report_versions", "progress")
