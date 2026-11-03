"""Explicit legacy provenance and transactional admission gate.

Revision ID: 08b2c3d4e5f6
Revises: 07a1b2c3d4e5
"""
import sqlalchemy as sa
from alembic import op

revision = "08b2c3d4e5f6"
down_revision = "07a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("household_profiles", sa.Column(
        "legacy_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'"),
    ))
    gate = op.create_table(
        "runtime_admission", sa.Column("id", sa.String(32), primary_key=True),
    )
    op.bulk_insert(gate, [{"id": "agent_turn"}])
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


def downgrade():
    op.drop_table("legacy_imports")
    op.drop_table("runtime_admission")
    op.drop_column("household_profiles", "legacy_details")
