"""Mark sessions that may authenticate native app requests.

Revision ID: 1a4b6c8d0e2f
Revises: 09c3d4e5f6a7
"""

import sqlalchemy as sa
from alembic import op

revision = "1a4b6c8d0e2f"
down_revision = "09c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_sessions",
        sa.Column("native_client", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade():
    op.drop_column("auth_sessions", "native_client")
