"""add image and reviews to cooking records

Revision ID: e5f9a0b1c2d3
Revises: d4e7f8a9b0c1
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f9a0b1c2d3"
down_revision: Union[str, None] = "d4e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 早期开发环境通过 metadata.create_all 已经拥有这些列，但 alembic_version
    # 仍停留在上一版，因此迁移必须检查列是否存在以便安全补跑。
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cooking_records")}
    if "image" not in columns:
        op.add_column("cooking_records", sa.Column("image", sa.Text(), nullable=False, server_default=""))
    if "reviews" not in columns:
        op.add_column("cooking_records", sa.Column("reviews", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cooking_records")}
    if "reviews" in columns:
        op.drop_column("cooking_records", "reviews")
    if "image" in columns:
        op.drop_column("cooking_records", "image")
