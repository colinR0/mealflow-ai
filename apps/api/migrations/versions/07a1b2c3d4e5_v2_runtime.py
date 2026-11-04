"""v2 identity, durable runs, actions and versioned knowledge.

Revision ID: 07a1b2c3d4e5
Revises: b9d2e3f4a5b6
"""

import sqlalchemy as sa
from alembic import op

revision = "07a1b2c3d4e5"
down_revision = "b9d2e3f4a5b6"
branch_labels = None
depends_on = None


def col(name, kind, nullable=False, fk=None, primary=False):
    args = [sa.ForeignKey(fk)] if fk else []
    return sa.Column(name, kind, *args, nullable=nullable, primary_key=primary)


def table(name, *columns, timestamps=True, identity=True):
    prefix = [col("id", sa.String(36), primary=True)] if identity else []
    suffix = (
        [
            col("created_at", sa.DateTime(timezone=True)),
            col("updated_at", sa.DateTime(timezone=True)),
        ]
        if timestamps
        else []
    )
    op.create_table(name, *prefix, *columns, *suffix)


def s(name, length=36, nullable=False, fk=None):
    return col(name, sa.String(length), nullable, fk)


def j(name):
    return col(name, sa.JSON())


def dt(name, nullable=False):
    return col(name, sa.DateTime(timezone=True), nullable)


def upgrade():
    table(
        "auth_sessions",
        s("user_id", fk="users.id"),
        s("token_hash", 64),
        s("csrf_hash", 64),
        dt("expires_at"),
        dt("idle_until"),
        col("revoked", sa.Boolean()),
        sa.UniqueConstraint("token_hash"),
    )
    table(
        "login_attempts",
        col("key", sa.String(64), primary=True),
        col("failures", sa.Integer()),
        dt("window_until"),
        timestamps=False,
        identity=False,
    )
    table(
        "household_profiles",
        s("workspace_id", fk="workspaces.id"),
        s("display_name", 80),
        s("linked_user_id", nullable=True, fk="users.id"),
        s("legacy_user_id", nullable=True),
        j("restrictions"),
        j("preferences"),
        col("version", sa.Integer()),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    table(
        "household_invites",
        s("workspace_id", fk="workspaces.id"),
        s("token_hash", 64),
        dt("expires_at"),
        dt("used_at", True),
        sa.UniqueConstraint("token_hash"),
    )
    table(
        "inventory_lots",
        s("workspace_id", fk="workspaces.id"),
        s("ingredient_key", 80),
        s("name", 120),
        s("quantity_status", 16),
        col("quantity", sa.Numeric(12, 3), True),
        s("unit", 20, True),
        col("expires_on", sa.Date(), True),
        dt("confirmed_at"),
        s("source_type", 32),
        s("source_id", 120),
        col("version", sa.Integer()),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "source_type", "source_id"),
        sa.CheckConstraint(
            "(quantity_status = 'unknown' AND quantity IS NULL AND unit IS NULL) OR "
            "(quantity_status = 'known' AND quantity IS NOT NULL AND quantity >= 0 "
            "AND unit IS NOT NULL)",
            name="ck_lot_quantity",
        ),
    )
    table(
        "inventory_movements",
        s("workspace_id"),
        s("lot_id"),
        s("actor_id", fk="users.id"),
        s("kind", 32),
        j("before"),
        j("after"),
        s("source_type", 32),
        s("source_id", 120),
        sa.ForeignKeyConstraint(
            ["workspace_id", "lot_id"], ["inventory_lots.workspace_id", "inventory_lots.id"]
        ),
        sa.UniqueConstraint("workspace_id", "source_type", "source_id"),
    )
    table(
        "shopping_items",
        s("workspace_id", fk="workspaces.id"),
        s("name", 120),
        s("ingredient_key", 80),
        s("quantity_status", 16),
        col("quantity", sa.Numeric(12, 3), True),
        s("unit", 20, True),
        s("note", 300),
        s("status", 20),
        j("sources"),
        col("version", sa.Integer()),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    table(
        "turn_sessions",
        s("workspace_id", fk="workspaces.id"),
        s("created_by", fk="users.id"),
        j("state"),
        s("active_run_id", nullable=True),
        sa.UniqueConstraint("workspace_id", "id"),
    )
    table(
        "agent_turn_runs",
        s("workspace_id", fk="workspaces.id"),
        s("actor_id", fk="users.id"),
        s("session_id"),
        s("parent_run_id", nullable=True, fk="agent_turn_runs.id"),
        j("request"),
        j("state"),
        j("result"),
        s("status", 32),
        col("cancel_requested", sa.Boolean()),
        col("next_seq", sa.Integer()),
        dt("events_expire_at"),
        s("error_code", 80, True),
        sa.UniqueConstraint("workspace_id", "id"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "session_id"], ["turn_sessions.workspace_id", "turn_sessions.id"]
        ),
    )
    table(
        "agent_events",
        s("run_id", fk="agent_turn_runs.id"),
        col("seq", sa.Integer()),
        s("type", 40),
        j("payload"),
        dt("created_at"),
        sa.UniqueConstraint("run_id", "seq"),
        timestamps=False,
    )
    table(
        "tool_executions",
        s("run_id", fk="agent_turn_runs.id"),
        s("tool_call_id", 120),
        s("input_hash", 64),
        s("status", 24),
        j("result"),
        col("duration_ms", sa.Integer()),
        sa.UniqueConstraint("run_id", "tool_call_id"),
    )
    table(
        "pending_actions",
        s("run_id", nullable=True),
        s("workspace_id", fk="workspaces.id"),
        s("actor_id", fk="users.id"),
        s("type", 40),
        j("payload"),
        s("payload_hash", 64),
        col("version", sa.Integer()),
        s("status", 24),
        dt("expires_at"),
        j("result"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["agent_turn_runs.workspace_id", "agent_turn_runs.id"]
        ),
    )
    table(
        "background_jobs",
        s("kind", 32),
        s("business_id"),
        s("dedupe_key", 160),
        s("status", 24),
        col("attempts", sa.Integer()),
        dt("next_run_at"),
        dt("lease_until", True),
        s("worker_id", 80, True),
        sa.UniqueConstraint("dedupe_key"),
    )
    table(
        "idempotency_records",
        s("workspace_id", fk="workspaces.id"),
        s("actor_id", fk="users.id"),
        s("operation", 80),
        s("key", 120),
        s("request_hash", 64),
        j("result"),
        sa.UniqueConstraint("workspace_id", "actor_id", "operation", "key"),
    )
    table(
        "audit_events",
        s("workspace_id", fk="workspaces.id"),
        s("actor_id", fk="users.id"),
        s("action", 80),
        s("object_id"),
        j("detail"),
    )
    table(
        "knowledge_sources",
        s("name", 160),
        s("approval", 24),
        s("license_note", 300),
        s("url", 500, True),
    )
    table(
        "embedding_profiles",
        s("model", 120),
        col("dimensions", sa.Integer()),
        s("template_version", 40),
        s("tokenizer_version", 40),
        col("is_fake", sa.Boolean()),
    )
    table(
        "index_generations",
        s("profile_id", fk="embedding_profiles.id"),
        col("active", sa.Boolean()),
        s("status", 24),
    )
    op.create_index(
        "uq_one_active_generation",
        "index_generations",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )
    table(
        "knowledge_documents",
        s("recipe_id", fk="recipes.id"),
        s("source_id", fk="knowledge_sources.id"),
        col("content_version", sa.Integer()),
        col("published", sa.Boolean()),
        j("capabilities"),
        j("content"),
        col("search_text", sa.Text()),
        sa.UniqueConstraint("recipe_id"),
    )
    table(
        "knowledge_chunks",
        s("document_id", fk="knowledge_documents.id"),
        col("content_version", sa.Integer()),
        s("generation_id", fk="index_generations.id"),
        col("ordinal", sa.Integer()),
        col("text", sa.Text()),
        j("embedding"),
        sa.UniqueConstraint("document_id", "content_version", "generation_id", "ordinal"),
    )
    for name, column in [
        ("auth_sessions", "user_id"),
        ("household_profiles", "workspace_id"),
        ("inventory_lots", "workspace_id"),
        ("shopping_items", "workspace_id"),
        ("turn_sessions", "workspace_id"),
        ("agent_turn_runs", "workspace_id"),
        ("agent_turn_runs", "status"),
        ("agent_events", "run_id"),
        ("tool_executions", "run_id"),
        ("pending_actions", "workspace_id"),
        ("background_jobs", "status"),
        ("knowledge_chunks", "document_id"),
    ]:
        op.create_index(f"ix_{name}_{column}", name, [column])


def downgrade():
    for name in [
        "knowledge_chunks",
        "knowledge_documents",
        "index_generations",
        "embedding_profiles",
        "knowledge_sources",
        "audit_events",
        "idempotency_records",
        "background_jobs",
        "pending_actions",
        "tool_executions",
        "agent_events",
        "agent_turn_runs",
        "turn_sessions",
        "shopping_items",
        "inventory_movements",
        "inventory_lots",
        "household_invites",
        "household_profiles",
        "login_attempts",
        "auth_sessions",
    ]:
        op.drop_table(name)
