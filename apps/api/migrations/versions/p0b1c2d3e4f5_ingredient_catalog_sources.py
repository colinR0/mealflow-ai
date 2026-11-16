"""Separate inventory dictionary entries from unreviewed recipe imports."""

from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "p0b1c2d3e4f5"
down_revision = "n9a0b1c2d3e4"
branch_labels = None
depends_on = None

PRESET_KEYS = (
    "tomato", "potato", "greens", "green_beans", "pepper_green", "carrot", "onion",
    "mushroom", "cucumber", "eggplant", "winter_melon", "cilantro", "garlic", "ginger",
    "scallion", "pork", "chicken_breast", "chicken_leg", "beef", "egg", "milk", "fish",
    "shrimp", "tofu", "rice", "noodles", "flour", "peanut", "seaweed", "soy_sauce",
    "salt", "oil", "vinegar", "doubanjiang", "starch", "dried_chili",
)


def upgrade() -> None:
    op.add_column("ingredients", sa.Column(
        "catalog_source", sa.String(24), nullable=False, server_default="preset",
    ))
    # Imported recipe fragments remain linked to recipes, never public dictionary suggestions.
    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT id, key FROM ingredients")).mappings():
        imported_id = str(uuid5(
            NAMESPACE_URL, "mealflow:howtocook-rag:v1:ingredient:" + row["key"],
        ))
        if row["id"] == imported_id and row["key"] not in PRESET_KEYS:
            connection.execute(sa.text(
                "UPDATE ingredients SET catalog_source = 'recipe_import' WHERE id = :id"
            ), {"id": row["id"]})
    op.execute("UPDATE ingredients SET catalog_source = 'user' WHERE key LIKE 'custom_%'")


def downgrade() -> None:
    op.drop_column("ingredients", "catalog_source")
