"""Household cooking logs, dishes, photos and comments."""

import sqlalchemy as sa
from alembic import op

revision = "m8f9a0b1c2d3"
down_revision = "l7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "cooking_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("cooked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "allow_assistant_citation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_ts(),
    )
    op.create_index("ix_cooking_logs_workspace_id", "cooking_logs", ["workspace_id"])
    op.create_index("ix_cooking_logs_creator_id", "cooking_logs", ["creator_id"])
    op.create_index(
        "ix_cooking_logs_workspace_cooked_at", "cooking_logs", ["workspace_id", "cooked_at"]
    )

    op.create_table(
        "cooking_log_dishes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "log_id",
            sa.String(36),
            sa.ForeignKey("cooking_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipe_id", sa.String(36), sa.ForeignKey("recipes.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_ts(),
    )
    op.create_index("ix_cooking_log_dishes_log_id", "cooking_log_dishes", ["log_id"])

    op.create_table(
        "cooking_log_photos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "log_id",
            sa.String(36),
            sa.ForeignKey("cooking_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(300), nullable=False, unique=True),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_ts(),
    )
    op.create_index("ix_cooking_log_photos_log_id", "cooking_log_photos", ["log_id"])

    op.create_table(
        "cooking_log_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "log_id",
            sa.String(36),
            sa.ForeignKey("cooking_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        *_ts(),
    )
    op.create_index("ix_cooking_log_comments_log_id", "cooking_log_comments", ["log_id"])
    op.create_index("ix_cooking_log_comments_author_id", "cooking_log_comments", ["author_id"])


def downgrade() -> None:
    op.drop_table("cooking_log_comments")
    op.drop_table("cooking_log_photos")
    op.drop_table("cooking_log_dishes")
    op.drop_table("cooking_logs")
