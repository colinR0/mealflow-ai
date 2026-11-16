"""Allow unknown recipe ingredient amounts and deduplicate links."""

import sqlalchemy as sa
from alembic import op

revision = "n9a0b1c2d3e4"
down_revision = "m8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "recipe_ingredients",
        "quantity",
        existing_type=sa.Numeric(12, 3),
        nullable=True,
    )
    op.alter_column(
        "recipe_ingredients",
        "unit",
        existing_type=sa.String(20),
        nullable=True,
    )
    op.create_unique_constraint(
        "uq_recipe_ingredients_recipe_ingredient",
        "recipe_ingredients",
        ["recipe_id", "ingredient_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_recipe_ingredients_recipe_ingredient",
        "recipe_ingredients",
        type_="unique",
    )
    # Downgrade is only safe after removing unknown amounts from the table.
    op.alter_column(
        "recipe_ingredients",
        "unit",
        existing_type=sa.String(20),
        nullable=False,
    )
    op.alter_column(
        "recipe_ingredients",
        "quantity",
        existing_type=sa.Numeric(12, 3),
        nullable=False,
    )