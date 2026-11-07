"""Bind inventory lots to stable ingredient ids.

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6

Safe backfill rules:
- Map only when inventory_lots.ingredient_key exactly equals ingredients.key.
- Unmapped lots keep their own ingredient_key/name by creating or reusing a
  custom Ingredient keyed by that exact key (no fuzzy rename onto a preset).
- Duplicate (workspace_id, ingredient_id) groups abort the upgrade with an
  explicit error listing lot ids; nothing is merged or quantity-accumulated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "g2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingredient_aliases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "ingredient_id",
            sa.String(length=36),
            sa.ForeignKey("ingredients.id"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(length=120), nullable=False),
        sa.Column("normalized_alias", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("normalized_alias"),
    )
    op.create_index(
        "ix_ingredient_aliases_ingredient_id",
        "ingredient_aliases",
        ["ingredient_id"],
    )
    op.create_index(
        "ix_ingredient_aliases_normalized_alias",
        "ingredient_aliases",
        ["normalized_alias"],
    )

    op.add_column(
        "inventory_lots",
        sa.Column("ingredient_id", sa.String(length=36), nullable=True),
    )

    conn = op.get_bind()
    # Exact key match onto the catalog only; never guess by display name.
    conn.execute(text(
        """
        UPDATE inventory_lots AS lot
        SET ingredient_id = ingredient.id
        FROM ingredients AS ingredient
        WHERE lot.ingredient_id IS NULL
          AND lot.ingredient_key = ingredient.key
        """
    ))

    unmatched = conn.execute(text(
        """
        SELECT id, ingredient_key, name
        FROM inventory_lots
        WHERE ingredient_id IS NULL
        ORDER BY id
        """
    )).mappings().all()
    for row in unmatched:
        key = row["ingredient_key"]
        existing = conn.execute(
            text("SELECT id FROM ingredients WHERE key = :key"),
            {"key": key},
        ).scalar()
        if existing is None:
            ingredient_id = row["id"]
            conn.execute(
                text(
                    """
                    INSERT INTO ingredients (
                        id, key, name, category, default_unit, reference_price,
                        price_source, price_updated_at, created_at, updated_at
                    ) VALUES (
                        :id, :key, :name, '其他', '份', NULL,
                        '迁移保留的自定义食材，暂无参考价', NULL,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"id": ingredient_id, "key": key, "name": row["name"][:80]},
            )
            existing = ingredient_id
        conn.execute(
            text(
                "UPDATE inventory_lots SET ingredient_id = :iid WHERE id = :lid"
            ),
            {"iid": existing, "lid": row["id"]},
        )

    conflicts = conn.execute(text(
        """
        SELECT workspace_id, ingredient_id,
               string_agg(id, ',' ORDER BY id) AS lot_ids,
               count(*) AS n
        FROM inventory_lots
        GROUP BY workspace_id, ingredient_id
        HAVING count(*) > 1
        ORDER BY workspace_id, ingredient_id
        """
    )).mappings().all()
    if conflicts:
        detail = "; ".join(
            f"{row['workspace_id']}/{row['ingredient_id']} -> {row['lot_ids']}"
            for row in conflicts
        )
        raise RuntimeError(
            "inventory_lots has duplicate workspace+ingredient rows; "
            f"resolve manually before retrying migration: {detail}"
        )

    op.alter_column("inventory_lots", "ingredient_id", nullable=False)
    op.create_foreign_key(
        "fk_inventory_lots_ingredient_id",
        "inventory_lots",
        "ingredients",
        ["ingredient_id"],
        ["id"],
    )
    op.create_index(
        "ix_inventory_lots_ingredient_id",
        "inventory_lots",
        ["ingredient_id"],
    )
    op.create_unique_constraint(
        "uq_inventory_lots_workspace_ingredient",
        "inventory_lots",
        ["workspace_id", "ingredient_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_inventory_lots_workspace_ingredient",
        "inventory_lots",
        type_="unique",
    )
    op.drop_index("ix_inventory_lots_ingredient_id", table_name="inventory_lots")
    op.drop_constraint(
        "fk_inventory_lots_ingredient_id",
        "inventory_lots",
        type_="foreignkey",
    )
    op.drop_column("inventory_lots", "ingredient_id")
    op.drop_index(
        "ix_ingredient_aliases_normalized_alias",
        table_name="ingredient_aliases",
    )
    op.drop_index(
        "ix_ingredient_aliases_ingredient_id",
        table_name="ingredient_aliases",
    )
    op.drop_table("ingredient_aliases")
