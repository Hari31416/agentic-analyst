"""Add versioned document summary cache."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8c204d39f51"
down_revision = "98657a061bfe"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "summary_cache",
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index(
        op.f("ix_summary_cache_scope"), "summary_cache", ["scope"], unique=False
    )


def downgrade():
    op.drop_index(op.f("ix_summary_cache_scope"), table_name="summary_cache")
    op.drop_table("summary_cache")
