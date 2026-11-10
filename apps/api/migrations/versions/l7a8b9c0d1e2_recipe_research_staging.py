"""Add an isolated research index for unverified recipe chunks.

Revision ID: l7a8b9c0d1e2
Revises: h3c4d5e6f7a8

The staging schema holds the local BGE 512-d pilot. It does not alter the
business recipe table or the existing 64-d demo embedding column.
"""

from alembic import op

revision = "l7a8b9c0d1e2"
down_revision = "h3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS recipe_research")
    op.execute(
        """
        CREATE TABLE recipe_research.parents (
            document_id varchar(64) PRIMARY KEY,
            source_asset_key varchar(1024) NOT NULL,
            source_sha256 varchar(64) NOT NULL,
            source_record_key varchar(1024),
            name varchar(200) NOT NULL,
            content_sha256 varchar(64) NOT NULL,
            publishable boolean NOT NULL,
            chunkable boolean NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE recipe_research.build_batches (
            id varchar(36) PRIMARY KEY,
            corpus_sha256 varchar(64) NOT NULL,
            chunks_sha256 varchar(64) NOT NULL,
            policy_version varchar(80) NOT NULL,
            model_name varchar(200) NOT NULL,
            dimension integer NOT NULL,
            query_prefix text NOT NULL,
            instruction_version varchar(80) NOT NULL,
            normalize boolean NOT NULL,
            status varchar(40) NOT NULL,
            chunk_count integer NOT NULL DEFAULT 0,
            parent_count integer NOT NULL DEFAULT 0,
            error text,
            production_publishable boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT recipe_research_batches_not_production
                CHECK (production_publishable = false)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE recipe_research.chunks (
            id bigserial PRIMARY KEY,
            batch_id varchar(36) NOT NULL
                REFERENCES recipe_research.build_batches (id),
            chunk_id varchar(64) NOT NULL,
            document_id varchar(64) NOT NULL,
            content_version varchar(64) NOT NULL,
            section varchar(32) NOT NULL,
            step_ordinals jsonb NOT NULL,
            text text NOT NULL,
            embedding vector(512),
            search_tsv tsvector,
            created_at timestamptz NOT NULL,
            UNIQUE (batch_id, chunk_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_recipe_research_chunks_embedding_hnsw
        ON recipe_research.chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_recipe_research_chunks_search_tsv
        ON recipe_research.chunks
        USING gin (search_tsv)
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS recipe_research CASCADE")
