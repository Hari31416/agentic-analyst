"""Local JWT accounts and revoked access tokens."""

from alembic import op
import sqlalchemy as sa

revision = "d37e41b5a002"
down_revision = "c2d14e6a901f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("username", sa.String(50), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(36), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])


def downgrade():
    op.drop_table("revoked_tokens")
    op.drop_table("users")
