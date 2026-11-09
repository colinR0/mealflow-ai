"""Add isolated substitution, flavor-template and food-risk libraries."""

import sqlalchemy as sa
from alembic import op

revision = "j5e6f7a8b9c0"
down_revision = "i4d5e6f7a8b9"
branch_labels = None
depends_on = None


def _common_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("rag_text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("embedding_profile_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ]


def _common_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_review_status", table, ["review_status"])
    op.create_index(f"ix_{table}_embedding_profile_id", table, ["embedding_profile_id"])


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "ingredient_substitutions",
        *_common_columns(),
        sa.Column("from_ingredient_key", sa.String(80), nullable=False),
        sa.Column("from_ingredient_name", sa.String(80), nullable=False),
        sa.Column("to_ingredient_key", sa.String(80), nullable=False),
        sa.Column("to_ingredient_name", sa.String(80), nullable=False),
        sa.Column("conversion_factor", sa.Numeric(8, 4), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(20), nullable=False, server_default=""),
        sa.Column("diet_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("exclude_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("flavor_impact", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("from_ingredient_key", "to_ingredient_key",
                            name="uq_substitution_pair"),
        sa.CheckConstraint("conversion_factor > 0", name="ck_substitution_factor"),
        sa.CheckConstraint("priority >= 0", name="ck_substitution_priority"),
        sa.CheckConstraint("review_status IN ('draft', 'approved', 'revoked')",
                           name="ck_substitution_review_status"),
    )
    op.create_index("ix_ingredient_substitutions_from_key", "ingredient_substitutions",
                    ["from_ingredient_key"])
    op.create_index("ix_ingredient_substitutions_to_key", "ingredient_substitutions",
                    ["to_ingredient_key"])
    _common_indexes("ingredient_substitutions")

    op.create_table(
        "flavor_templates",
        *_common_columns(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("flavor_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("components", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("ratio_basis", sa.String(16), nullable=False, server_default="parts"),
        sa.Column("usage", sa.Text(), nullable=False, server_default=""),
        sa.Column("cautions", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.CheckConstraint("review_status IN ('draft', 'approved', 'revoked')",
                           name="ck_flavor_template_review_status"),
        sa.CheckConstraint("ratio_basis IN ('parts', 'weight', 'volume')",
                           name="ck_flavor_template_ratio_basis"),
    )
    op.create_index("ix_flavor_templates_name", "flavor_templates", ["name"], unique=True)
    _common_indexes("flavor_templates")

    op.create_table(
        "food_risk_rules",
        *_common_columns(),
        sa.Column("risk_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("applies_to", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("min_age_months", sa.Integer(), nullable=True),
        sa.Column("max_age_months", sa.Integer(), nullable=True),
        sa.Column("condition_tags", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::json")),
        sa.Column("ingredient_keys", sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::json")),
        sa.Column("advice", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_level", sa.String(24), nullable=False, server_default="unverified"),
        sa.CheckConstraint("severity IN ('info', 'warning', 'danger')",
                           name="ck_food_risk_severity"),
        sa.CheckConstraint("evidence_level IN ('established', 'limited', 'unverified')",
                           name="ck_food_risk_evidence"),
        sa.CheckConstraint("review_status IN ('draft', 'approved', 'revoked')",
                           name="ck_food_risk_review_status"),
        sa.CheckConstraint("min_age_months IS NULL OR min_age_months >= 0",
                           name="ck_food_risk_min_age"),
        sa.CheckConstraint("max_age_months IS NULL OR min_age_months IS NULL "
                           "OR max_age_months >= min_age_months",
                           name="ck_food_risk_age_range"),
    )
    op.create_index("ix_food_risk_rules_risk_type", "food_risk_rules", ["risk_type"])
    _common_indexes("food_risk_rules")

    for table in ("ingredient_substitutions", "flavor_templates", "food_risk_rules"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN embedding_vector vector(64)"
        )
        op.execute(
            f"CREATE INDEX ix_{table}_embedding_hnsw ON {table} "
            "USING hnsw (embedding_vector vector_cosine_ops) "
            "WHERE embedding_vector IS NOT NULL"
        )
        op.execute(f"ALTER TABLE {table} ADD COLUMN search_tsv tsvector")
        op.execute(
            f"CREATE INDEX ix_{table}_search_tsv ON {table} USING gin (search_tsv)"
        )


def downgrade() -> None:
    op.drop_table("food_risk_rules")
    op.drop_table("flavor_templates")
    op.drop_table("ingredient_substitutions")