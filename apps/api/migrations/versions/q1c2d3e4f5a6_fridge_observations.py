"""Unconfirmed still-photo detections from the local SAM service."""

import sqlalchemy as sa
from alembic import op

revision = "q1c2d3e4f5a6"
down_revision = "p0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fridge_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'discarded')",
            name="ck_fridge_observation_status",
        ),
    )
    op.create_index(
        "ix_fridge_observations_workspace_status",
        "fridge_observations",
        ["workspace_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_fridge_observations_workspace_status", table_name="fridge_observations")
    op.drop_table("fridge_observations")
