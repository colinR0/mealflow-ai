"""family kitchen pivot

V2 家庭厨房改造:
- 下线政务评分终端:drop feedback、kiosk_devices
- 家庭档案:dietary_restrictions 加 user_id(成员级忌口),新增 member_events 特殊事件表
- 物价溯源:ingredients 加 reference_price / price_source / price_updated_at
- 口味冲突检测:recipes 加 taste_tags
- 对话工作台:新增 agent_chat_sessions / agent_chat_messages

Revision ID: a7f3c2d91e44
Revises: 2df84fa3e330
Create Date: 2026-10-06
"""

from alembic import op
import sqlalchemy as sa

revision = "a7f3c2d91e44"
down_revision = "2df84fa3e330"
branch_labels = None
depends_on = None

PRICE_SOURCE = "市区大型超市公开参考价"


def upgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("kiosk_devices")

    # 历史版本只添加成员引用列，保持既有迁移结构。
    with op.batch_alter_table("dietary_restrictions") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(36), nullable=True))
    op.create_index("ix_dietary_restrictions_user_id", "dietary_restrictions", ["user_id"])

    op.create_table(
        "member_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), index=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("constraint_tags", sa.JSON(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column("ingredients", sa.Column("reference_price", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "ingredients",
        sa.Column("price_source", sa.String(120), nullable=False, server_default=PRICE_SOURCE),
    )
    op.add_column("ingredients", sa.Column("price_updated_at", sa.DateTime(timezone=True)))

    op.add_column("recipes", sa.Column("taste_tags", sa.JSON(), nullable=False, server_default="[]"))

    op.create_table(
        "agent_chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), index=True),
        sa.Column("title", sa.String(120), nullable=False, server_default="新会话"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("agent_chat_sessions.id"), index=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("thinking_text", sa.Text(), nullable=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("meal_plans.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_chat_messages")
    op.drop_table("agent_chat_sessions")
    op.drop_column("recipes", "taste_tags")
    op.drop_column("ingredients", "price_updated_at")
    op.drop_column("ingredients", "price_source")
    op.drop_column("ingredients", "reference_price")
    op.drop_table("member_events")
    op.drop_index("ix_dietary_restrictions_user_id", table_name="dietary_restrictions")
    op.drop_column("dietary_restrictions", "user_id")
    op.create_table(
        "kiosk_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(80), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("site_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("meal_service_id", sa.String(36), nullable=False),
        sa.Column("meal_plan_id", sa.String(36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("comment", sa.String(300), nullable=True),
        sa.Column("leftover_level", sa.String(32), nullable=True),
        sa.Column("is_suspicious", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
