"""Record artifact purpose for output filtering. No historical classification."""

from alembic import op
import sqlalchemy as sa

revision = "b19d4a2e730f"
down_revision = "d37e41b5a002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "artifacts",
        sa.Column("role", sa.String(30), nullable=False, server_default="intermediate"),
    )
    op.create_check_constraint(
        "ck_artifacts_role",
        "artifacts",
        "role IN ('output', 'intermediate', 'execution_code', 'input_snapshot', 'metadata')",
    )


def downgrade():
    op.drop_constraint("ck_artifacts_role", "artifacts", type_="check")
    op.drop_column("artifacts", "role")
