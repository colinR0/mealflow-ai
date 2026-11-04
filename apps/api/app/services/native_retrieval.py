"""Native PostgreSQL hybrid retrieval for the S5 knowledge index.

This module deliberately does not change the v2 ORM models.  Install the
PostgreSQL schema with :func:`install_native_schema` before using it.  The
embedding argument is a provider boundary; the offline provider is marked
fake and must not be used as a quality claim.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import jieba
from sqlalchemy import Connection, text

from app.services.hybrid_retrieval import RetrievalHit, reciprocal_rank_fusion

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS native_generation_id varchar(36);
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_vector vector({dimensions});
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS search_tsv tsvector;
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw
  ON knowledge_chunks USING hnsw (embedding_vector vector_cosine_ops)
  WHERE embedding_vector IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_search_tsv
  ON knowledge_chunks USING gin (search_tsv);
"""


def tokenize_chinese(value: str) -> str:
    """Return space-delimited jieba tokens for PostgreSQL's simple config."""
    tokens = [token.strip().lower() for token in jieba.lcut(value, cut_all=False)]
    return " ".join(token for token in tokens if token and not re.fullmatch(r"\s+", token))


def vector_literal(values: Sequence[float]) -> str:
    if not values or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("embedding must be a non-empty finite numeric vector (no bools)")
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def install_native_schema(connection: Connection, dimensions: int = 64) -> None:
    """Install native columns/indexes; raises a useful error when pgvector is absent."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("S5 native schema requires PostgreSQL")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("dimensions must be positive")
    try:
        statements = [
            item.strip()
            for item in SCHEMA_SQL.format(dimensions=dimensions).split(";")
            if item.strip()
        ]
        for statement in statements:
            connection.exec_driver_sql(statement)
    except Exception as exc:  # Translate extension setup failure into a deployment error.
        raise RuntimeError(
            "pgvector is unavailable; install the PostgreSQL vector extension"
        ) from exc


@dataclass(frozen=True)
class NativeHit:
    recipe_id: str
    chunk_id: str
    score: float
    excerpt: str
    source_id: str
    content_version: int


class NativeRetriever:
    def __init__(self, connection: Connection, *, profile_id: str, dimensions: int = 64):
        self.connection = connection
        self.profile_id = profile_id
        self.dimensions = dimensions

    def search(self, query: str, embedding: Sequence[float], limit: int = 8) -> list[NativeHit]:
        if len(embedding) != self.dimensions:
            raise ValueError("query embedding dimension does not match the index profile")
        query_tokens = tokenize_chinese(query)
        params = {"profile_id": self.profile_id, "query": query_tokens,
                  "vector": vector_literal(embedding), "channel_limit": max(limit * 8, 40)}
        # Both candidate lists are ranked in SQL.  Approval/version predicates
        # are repeated in each channel so revoked content cannot leak through RRF.
        sql = text("""
            WITH active AS (
              SELECT g.id FROM index_generations g
              WHERE g.active = true AND g.status = 'ready' AND g.profile_id = :profile_id
            ), keyword AS (
              SELECT c.id chunk_id, d.recipe_id, c.text excerpt, s.id source_id,
                     d.content_version, row_number() OVER (
                       ORDER BY ts_rank(c.search_tsv, plainto_tsquery('simple', :query)) DESC,
                                d.recipe_id, c.id) rank
              FROM knowledge_chunks c
              JOIN knowledge_documents d ON d.id = c.document_id
              JOIN knowledge_sources s ON s.id = d.source_id
              JOIN active a ON a.id = c.generation_id
                WHERE d.published = true AND s.approval = 'approved'
                AND c.content_version = d.content_version
                AND c.search_tsv @@ plainto_tsquery('simple', :query)
            ), vector AS (
              SELECT c.id chunk_id, d.recipe_id, c.text excerpt, s.id source_id,
                     d.content_version, row_number() OVER (
                       ORDER BY c.embedding_vector <=> CAST(:vector AS vector),
                                d.recipe_id, c.id) rank
              FROM knowledge_chunks c
              JOIN knowledge_documents d ON d.id = c.document_id
              JOIN knowledge_sources s ON s.id = d.source_id
              JOIN active a ON a.id = c.generation_id
              WHERE d.published = true AND s.approval = 'approved'
                AND c.content_version = d.content_version AND c.embedding_vector IS NOT NULL
            ), keyword_best AS (
              SELECT DISTINCT ON (recipe_id) * FROM keyword ORDER BY recipe_id, rank
            ), vector_best AS (
              SELECT DISTINCT ON (recipe_id) * FROM vector ORDER BY recipe_id, rank
            ), candidates AS (
              SELECT *, 'keyword' channel, 1.0 / (60 + rank) rrf
              FROM keyword_best WHERE rank <= :channel_limit
              UNION ALL
              SELECT *, 'vector' channel, 1.0 / (60 + rank) rrf
              FROM vector_best WHERE rank <= :channel_limit
            ), evidence AS (
              SELECT *, sum(rrf) OVER (PARTITION BY recipe_id) score,
                     row_number() OVER (
                       PARTITION BY recipe_id ORDER BY rank, channel, chunk_id) evidence_rank
              FROM candidates
            )
            SELECT recipe_id, chunk_id, score, excerpt, source_id, content_version
            FROM evidence WHERE evidence_rank = 1 ORDER BY score DESC, recipe_id LIMIT :limit
        """)
        return [NativeHit(**dict(row._mapping)) for row in self.connection.execute(
            sql, {**params, "limit": min(100, max(1, limit))}
        ).all()]


def rrf_hits(keyword: Sequence[RetrievalHit], vector: Sequence[RetrievalHit], limit: int = 8):
    """Small pure helper shared by SQL integration tests and diagnostics."""
    return reciprocal_rank_fusion((keyword, vector))[:limit]
