"""Index the language-specific lexical search expressions."""

from alembic import op
import sqlalchemy as sa

revision = "98657a061bfe"
down_revision = "6d92b49bc139"
branch_labels = None
depends_on = None


def upgrade():
    for config, predicate in (
        ("english", "language ILIKE 'en%'"),
        ("simple", "language NOT ILIKE 'en%'"),
    ):
        op.create_index(
            f"ix_document_chunks_{config}_fts",
            "document_chunks",
            [
                sa.text(
                    f"to_tsvector('{config}'::regconfig, (coalesce(heading, '') || ' ') || normalized_text)"
                )
            ],
            postgresql_using="gin",
            postgresql_where=sa.text(predicate),
        )


def downgrade():
    for config in ("simple", "english"):
        op.drop_index(f"ix_document_chunks_{config}_fts", table_name="document_chunks")
