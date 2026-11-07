"""Register recipe source files and immutable content versions.

Revision ID: h3c4d5e6f7a8
Revises: g2b3c4d5e6f7
"""

import sqlalchemy as sa
from alembic import op

revision = "h3c4d5e6f7a8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recipe_source_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recipe_source_asset_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(36),
            sa.ForeignKey("recipe_source_assets.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("parser_name", sa.String(80), nullable=True),
        sa.Column("parser_version", sa.String(40), nullable=True),
        sa.Column("parsed_uri", sa.String(1024), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "version"),
    )
    op.create_index(
        "ix_recipe_source_asset_versions_asset_id",
        "recipe_source_asset_versions",
        ["asset_id"],
    )
    op.create_index(
        "ix_recipe_source_asset_versions_status",
        "recipe_source_asset_versions",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recipe_source_asset_versions_status", table_name="recipe_source_asset_versions"
    )
    op.drop_index(
        "ix_recipe_source_asset_versions_asset_id", table_name="recipe_source_asset_versions"
    )
    op.drop_table("recipe_source_asset_versions")
    op.drop_table("recipe_source_assets")
