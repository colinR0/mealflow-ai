"""Drop vector and full-text columns from structured food knowledge libraries.

The three libraries are now retrieved by typed SQL lookup.  Their embedding,
profile, rag_text and tsvector columns are no longer written or read.
"""

import sqlalchemy as sa
from alembic import op

revision = "l7d8e9f0a1b2"
down_revision = "k6f7a8b9c0d1"
branch_labels = None
depends_on = None

TABLES = ("ingredient_substitutions", "flavor_templates", "food_risk_rules")
VECTOR_DIMENSIONS = 512


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_embedding_hnsw")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_search_tsv")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_embedding_profile_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS search_tsv")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS embedding_vector")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS embedding_profile_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS embedding")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS rag_text")


def downgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for table in TABLES:
        op.add_column(
            table, sa.Column("rag_text", sa.Text(), nullable=False, server_default="")
        )
        op.add_column(
            table,
            sa.Column(
                "embedding", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "embedding_profile_id", sa.String(36), nullable=False, server_default=""
            ),
        )
        op.create_index(f"ix_{table}_embedding_profile_id", table, ["embedding_profile_id"])
        op.alter_column(table, "rag_text", server_default=None)
        op.alter_column(table, "embedding_profile_id", server_default=None)
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN embedding_vector vector({VECTOR_DIMENSIONS})"
        )
        op.execute(
            f"CREATE INDEX ix_{table}_embedding_hnsw ON {table} "
            "USING hnsw (embedding_vector vector_cosine_ops) "
            "WHERE embedding_vector IS NOT NULL"
        )
        op.execute(f"ALTER TABLE {table} ADD COLUMN search_tsv tsvector")
        op.execute(f"CREATE INDEX ix_{table}_search_tsv ON {table} USING gin (search_tsv)")