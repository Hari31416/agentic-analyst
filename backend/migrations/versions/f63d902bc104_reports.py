"""Personal versioned reports and retained artifact copies."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f63d902bc104"
down_revision = "e42c851ab901"
branch_labels = None
depends_on = None


def identity():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "reports",
        *identity(),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for field in ("user_id", "workspace_id"):
        op.create_index(f"ix_reports_{field}", "reports", [field])
    op.create_table(
        "report_versions",
        *identity(),
        sa.Column(
            "report_id",
            sa.String(36),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("snapshot", json_type, nullable=False),
        sa.Column("document", json_type),
        sa.Column("pdf_key", sa.String(512)),
        sa.Column("pdf_sha256", sa.String(64)),
        sa.Column("pdf_size", sa.Integer()),
        sa.Column("error", sa.String(200)),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("base_version_id", sa.String(36)),
        sa.UniqueConstraint("report_id", "number", name="uq_report_version_number"),
        sa.CheckConstraint(
            "state IN ('queued', 'generating', 'ready', 'failed')",
            name="ck_report_version_state",
        ),
        sa.CheckConstraint(
            "mode IN ('initial', 'wording', 'restructure')",
            name="ck_report_version_mode",
        ),
    )
    op.create_index("ix_report_versions_report_id", "report_versions", ["report_id"])
    op.create_table(
        "report_assets",
        *identity(),
        sa.Column(
            "report_id",
            sa.String(36),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_artifact_id", sa.String(36), nullable=False),
        sa.Column("storage_key", sa.String(512), unique=True, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.UniqueConstraint(
            "report_id", "original_artifact_id", name="uq_report_asset_original"
        ),
    )
    op.create_index("ix_report_assets_report_id", "report_assets", ["report_id"])


def downgrade():
    op.drop_table("report_assets")
    op.drop_table("report_versions")
    op.drop_table("reports")
