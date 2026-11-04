"""Add unique phone credentials; email is retained only as legacy metadata."""

import sqlalchemy as sa
from alembic import op

revision = "3c6d8e0f1a2b"
down_revision = "2b5c7d9e0f1a"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "email", existing_type=sa.String(160), nullable=True)
    op.add_column("users", sa.Column("phone", sa.String(11), nullable=True))
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)


def downgrade():
    # Phone-only accounts have no historical email; a downgrade must not invent one.
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_column("users", "phone")
