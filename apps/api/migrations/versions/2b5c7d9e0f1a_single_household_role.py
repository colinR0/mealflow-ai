"""Unify household memberships under one user role.

Revision ID: 2b5c7d9e0f1a
Revises: 1a4b6c8d0e2f
"""

from alembic import op

revision = "2b5c7d9e0f1a"
down_revision = "1a4b6c8d0e2f"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE workspace_members SET role = 'user'")


def downgrade():
    # Historical distinctions cannot be reconstructed after normalization.
    op.execute("UPDATE workspace_members SET role = 'member'")
