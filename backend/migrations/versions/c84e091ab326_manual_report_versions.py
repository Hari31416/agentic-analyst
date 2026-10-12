"""Allow directly edited report versions."""

from alembic import op

revision = "c84e091ab326"
down_revision = "ba321c7d801a"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_report_version_mode", "report_versions", type_="check")
    op.create_check_constraint(
        "ck_report_version_mode",
        "report_versions",
        "mode IN ('initial', 'wording', 'restructure', 'manual')",
    )


def downgrade():
    # Preserve edited versions as wording revisions on rollback.
    op.execute("UPDATE report_versions SET mode = 'wording' WHERE mode = 'manual'")
    op.drop_constraint("ck_report_version_mode", "report_versions", type_="check")
    op.create_check_constraint(
        "ck_report_version_mode",
        "report_versions",
        "mode IN ('initial', 'wording', 'restructure')",
    )
