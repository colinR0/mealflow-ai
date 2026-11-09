"""Allow knowledge documents to represent non-recipe cooking skills."""

import sqlalchemy as sa
from alembic import op

revision = "i4d5e6f7a8b9"
down_revision = "h3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column(
        "document_type", sa.String(32), nullable=False, server_default="recipe"))
    op.add_column("knowledge_documents", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("knowledge_documents", sa.Column("source_path", sa.String(1024), nullable=True))
    op.alter_column("knowledge_documents", "recipe_id", nullable=True)
    op.create_index("ix_knowledge_documents_document_type", "knowledge_documents",
                    ["document_type"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM knowledge_documents WHERE recipe_id IS NULL)"
    )):
        raise RuntimeError("Cannot downgrade while non-recipe knowledge documents exist")
    op.drop_index("ix_knowledge_documents_document_type", table_name="knowledge_documents")
    op.alter_column("knowledge_documents", "recipe_id", nullable=False)
    op.drop_column("knowledge_documents", "source_path")
    op.drop_column("knowledge_documents", "title")
    op.drop_column("knowledge_documents", "document_type")
