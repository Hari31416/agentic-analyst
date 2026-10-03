"""Cascade owned records and give jobs/caches explicit lifecycle owners."""

from alembic import op
import sqlalchemy as sa

revision = "c2d14e6a901f"
down_revision = "a8c204d39f51"
branch_labels = None
depends_on = None

# Frozen ownership map for this revision. Every FK here refers to an owned
# record; the HTTP lifecycle service guards retained JSON provenance first.
TABLES = (
    "threads",
    "sources",
    "connections",
    "datasets",
    "runs",
    "messages",
    "jobs",
    "events",
    "tool_calls",
    "artifacts",
    "evidence",
    "audit_events",
    "documents",
    "document_blocks",
    "document_chunks",
    "index_generations",
    "chunk_embeddings",
)


def change_foreign_keys(ondelete):
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        for fk in inspector.get_foreign_keys(table):
            name = fk["name"]
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                name,
                table,
                fk["referred_table"],
                fk["constrained_columns"],
                fk["referred_columns"],
                ondelete=ondelete,
            )


def upgrade():
    change_foreign_keys("CASCADE")
    for column, target in (
        ("workspace_id", "workspaces"),
        ("document_id", "documents"),
    ):
        op.add_column("jobs", sa.Column(column, sa.String(36), nullable=True))
        op.create_foreign_key(
            f"jobs_{column}_fkey", "jobs", target, [column], ["id"], ondelete="CASCADE"
        )
        op.create_index(f"ix_jobs_{column}", "jobs", [column])
    op.execute("""
        UPDATE jobs j SET document_id = d.id
        FROM documents d
        WHERE j.kind IN ('ingest_document', 'index_document')
          AND j.payload->>'document_id' = d.id
    """)
    op.execute("""
        UPDATE jobs j SET workspace_id = s.workspace_id
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE j.document_id = d.id
    """)
    op.execute("""
        UPDATE jobs j SET workspace_id = t.workspace_id
        FROM runs r JOIN threads t ON t.id = r.thread_id
        WHERE j.run_id = r.id
    """)
    op.execute("""
        UPDATE jobs j SET workspace_id = w.id FROM workspaces w
        WHERE j.kind = 'crawl_documents' AND j.payload->>'workspace_id' = w.id
    """)
    op.add_column(
        "summary_cache", sa.Column("workspace_id", sa.String(36), nullable=True)
    )
    op.execute("""
        UPDATE summary_cache c SET workspace_id = s.workspace_id FROM sources s
        WHERE c.payload->'documents'->0->>'source_id' = s.id
    """)
    # Unattributable caches are disposable; saved evidence/answers are untouched.
    op.execute("DELETE FROM summary_cache WHERE workspace_id IS NULL")
    op.alter_column("summary_cache", "workspace_id", nullable=False)
    op.create_foreign_key(
        "summary_cache_workspace_id_fkey",
        "summary_cache",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_summary_cache_workspace_id", "summary_cache", ["workspace_id"])


def downgrade():
    op.drop_index("ix_summary_cache_workspace_id", table_name="summary_cache")
    op.drop_constraint(
        "summary_cache_workspace_id_fkey", "summary_cache", type_="foreignkey"
    )
    op.drop_column("summary_cache", "workspace_id")
    for column in ("document_id", "workspace_id"):
        op.drop_index(f"ix_jobs_{column}", table_name="jobs")
        op.drop_constraint(f"jobs_{column}_fkey", "jobs", type_="foreignkey")
        op.drop_column("jobs", column)
    change_foreign_keys(None)
