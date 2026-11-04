"""Remove retired import tracking.

Revision ID: 09c3d4e5f6a7
Revises: 08b2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "09c3d4e5f6a7"
down_revision = "08b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("legacy_imports")


def downgrade():
    op.create_table(
        "legacy_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("source_table", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("target_table", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "source", "source_table", "source_id"),
    )
