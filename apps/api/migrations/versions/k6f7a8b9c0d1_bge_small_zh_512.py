"""Move native embeddings from the fake 64-dimensional profile to BGE-small-zh 512."""

from alembic import op

revision = "k6f7a8b9c0d1"
down_revision = "j5e6f7a8b9c0"
branch_labels = None
depends_on = None

TABLES = (
    "knowledge_chunks",
    "ingredient_substitutions",
    "flavor_templates",
    "food_risk_rules",
)


def _replace_vector_column(table: str, dimensions: int) -> None:
    op.execute(f"DROP INDEX IF EXISTS ix_{table}_embedding_hnsw")
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS embedding_vector")
    op.execute(f"ALTER TABLE {table} ADD COLUMN embedding_vector vector({dimensions})")
    op.execute(
        f"CREATE INDEX ix_{table}_embedding_hnsw ON {table} "
        "USING hnsw (embedding_vector vector_cosine_ops) "
        "WHERE embedding_vector IS NOT NULL"
    )
    # Old vectors belong to a different model/profile and must not be mixed.
    op.execute(f"UPDATE {table} SET embedding = '[]'::json")


def _invalidate_active_generation() -> None:
    op.execute(
        "UPDATE index_generations SET active = false, status = 'failed' "
        "WHERE active = true"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    _invalidate_active_generation()
    for table in TABLES:
        _replace_vector_column(table, 512)


def downgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    _invalidate_active_generation()
    for table in TABLES:
        _replace_vector_column(table, 64)