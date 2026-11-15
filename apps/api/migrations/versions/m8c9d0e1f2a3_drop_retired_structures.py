"""Drop retired business, agent, and 64-d demo tables.

Revision ID: m8c9d0e1f2a3
Revises: l7a8b9c0d1e2

Historical revisions stay in place. This revision only removes structures the
current household app and six tools no longer use. recipe_research is kept.
"""

from alembic import op

revision = "m8c9d0e1f2a3"
down_revision = "l7a8b9c0d1e2"
branch_labels = None
depends_on = None

RETIRED_TABLES = (
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
    "agent_chat_messages",
    "tool_traces",
    "meal_plans",
    "agent_runs",
    "agent_chat_sessions",
    "cooking_records",
    "procurement_items",
    "inventory_items",
    "recipe_ingredients",
    "knowledge_chunks",
    "knowledge_documents",
    "recipes",
    "index_generations",
    "embedding_profiles",
    "knowledge_sources",
    "member_food_preferences",
    "member_events",
    "dietary_restrictions",
    "household_pantry_configs",
    "meal_services",
    "sites",
)


def upgrade() -> None:
    for name in RETIRED_TABLES:
        op.execute(f'DROP TABLE IF EXISTS public."{name}" CASCADE')


def downgrade() -> None:
    # Retired structures are recreated by replaying historical revisions, not here.
    raise NotImplementedError("retired tables are not recreated by this revision")
