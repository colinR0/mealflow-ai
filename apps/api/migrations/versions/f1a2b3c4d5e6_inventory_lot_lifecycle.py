"""Add lifecycle fields to inventory lots.

Revision ID: f1a2b3c4d5e6
Revises: 3c6d8e0f1a2b
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "3c6d8e0f1a2b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("inventory_lots", sa.Column(
        "notes", sa.String(300), nullable=False, server_default=""
    ))
    op.add_column("inventory_lots", sa.Column(
        "status", sa.String(16), nullable=False, server_default="active"
    ))


def downgrade():
    op.drop_column("inventory_lots", "status")
    op.drop_column("inventory_lots", "notes")
