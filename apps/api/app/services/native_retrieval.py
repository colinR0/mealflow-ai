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

from app.services.dish_aliases import alias_variants
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


# Function words and cooking filler carry no retrieval signal. Keeping them in an
# OR tsquery would make the keyword channel match almost every document.
_KEYWORD_STOPWORDS = frozenset({
    "的", "了", "和", "与", "及", "或", "是", "在", "有", "把", "就", "都", "也",
    "很", "还", "再", "又", "个", "份", "些", "这", "那", "我", "你", "他", "她",
    "它", "想", "要", "吃", "做", "用", "请", "帮", "给", "能", "可以", "一下",
    "吗", "呢", "吧", "啊", "呀", "么", "不", "没", "别", "太", "会", "该",
    "怎么", "怎样", "如何", "什么", "哪些", "多少", "为什么", "哪个",
    "做法", "步骤", "方法", "教程", "家常", "简单", "好吃", "推荐", "需要", "多久",
})
# A bare lexeme must still match, so single-character foods (虾/鱼/葱/蒜) survive.
_KEYWORD_TOKEN = re.compile(r"[\w\u3400-\u9fff]+")
# Quantities and measure words ("一个", "两张", "500克") describe a dish, they do
# not identify one, so they must not seed the keyword channel.
_KEYWORD_QUANTITY = re.compile(
    r"^[0-9零〇一二三四五六七八九十百千万两半几]+"
    r"[个份张只条块片碗杯勺克千克斤升毫升包袋盒瓶罐把根颗粒支串对双]?$"
)
_MAX_KEYWORD_TOKENS = 12
# Aliases widen the OR expression; cap the expanded set so the tsquery stays small.
_MAX_KEYWORD_TERMS = 24
_KEYWORD_DISABLED = "_nomatch_"


def keyword_query_tokens(query: str) -> list[str]:
    """Return OR-channel lexemes: deduped, stopword-free, punctuation stripped."""
    tokens: list[str] = []
    for raw in jieba.lcut(query, cut_all=False):
        token = raw.strip().lower()
        if not token or token in _KEYWORD_STOPWORDS:
            continue
        if not _KEYWORD_TOKEN.fullmatch(token) or token in tokens:
            continue
        if _KEYWORD_QUANTITY.match(token):
            continue
        tokens.append(token)
        if len(tokens) >= _MAX_KEYWORD_TOKENS:
            break
    # A single character only identifies a dish when it is the whole query (虾/鱼/
    # 葱). Mixed with real words it is a verb or filler ("买" in 我想买一个垃圾桶),
    # which matches unrelated chunks.
    if any(len(token) > 1 for token in tokens):
        tokens = [token for token in tokens if len(token) > 1]
    return tokens


def keyword_terms(query: str) -> list[str]:
    """Return query lexemes plus interchangeable dish-name aliases.

    The OR tsquery matches any term, so appending aliases only widens recall; the
    coverage threshold and the document-title tiers below keep precision.
    """
    return list(dict.fromkeys(
        term for group in keyword_term_groups(query) for term in group
    ))[:_MAX_KEYWORD_TERMS]


def keyword_term_groups(query: str) -> list[list[str]]:
    """Group each query lexeme with its aliases, for coverage counting.

    Coverage must count a group once: "西红柿炒鸡蛋" against the alias-expanded
    query [西红柿, 番茄, 蕃茄, 炒鸡蛋] has two groups, not four.
    """
    groups: list[list[str]] = []
    for token in keyword_query_tokens(query):
        variants = [token, *(v for v in alias_variants(token) if len(v) > 1)]
        groups.append(list(dict.fromkeys(variants)))
    return groups


def keyword_term_pairs(query: str) -> list[str]:
    """Flatten ``keyword_term_groups`` into ``"<group_id>\\x1f<term>"`` strings.

    psycopg cannot index a bound two-dimensional array, so the groups travel as a
    flat text array and the SQL splits them back apart. ``\\x1f`` (unit separator)
    cannot appear in a token that passed ``_KEYWORD_TOKEN``.
    """
    return [
        f"{group_id}\x1f{term}"
        for group_id, variants in enumerate(keyword_term_groups(query), start=1)
        for term in variants
    ]


def keyword_query(query: str) -> tuple[str, bool]:
    """Build the OR tsquery and report whether the keyword channel can run.

    ``plainto_tsquery`` joins lexemes with AND, so any natural-language question
    ("红烧肉的做法和步骤") matched nothing and the channel was effectively dead.
    OR restores it. Lexeme weights are deliberately NOT used: ``to_tsvector``
    stores every lexeme as weight D, so an ``:A`` label would match nothing.
    """
    tokens = keyword_terms(query)
    if not tokens:
        return _KEYWORD_DISABLED, False
    return " | ".join(tokens), True


def vector_literal(values: Sequence[float]) -> str:
    if not values or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("embedding must be a non-empty finite numeric vector (no bools)")
    return "[" + ",".join(str(float(value)) for value in values) + "]"


VECTOR_TABLES = ("knowledge_chunks",)


def _existing_vector_dimensions(connection: Connection) -> dict[str, int]:
    rows = connection.execute(
        text("""
            SELECT c.relname, a.atttypmod
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relname = ANY(:tables)
              AND a.attname = 'embedding_vector'
              AND NOT a.attisdropped
        """),
        {"tables": list(VECTOR_TABLES)},
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def install_native_schema(connection: Connection, dimensions: int = 64) -> None:
    """Install native columns/indexes; raises a useful error when pgvector is absent."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("S5 native schema requires PostgreSQL")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("dimensions must be positive")
    existing = _existing_vector_dimensions(connection)
    mismatched = {table: size for table, size in existing.items() if size != dimensions}
    if mismatched:
        raise RuntimeError(
            f"pgvector dimensions do not match the configured embedding: {mismatched}; "
            "run the matching Alembic migration and rebuild the index"
        )
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
    document_id: str
    recipe_id: str | None
    chunk_id: str
    score: float
    excerpt: str
    source_id: str
    content_version: int
    channels: str = ""
    similarity: float | None = None


class NativeRetriever:
    def __init__(self, connection: Connection, *, profile_id: str, dimensions: int = 64):
        self.connection = connection
        self.profile_id = profile_id
        self.dimensions = dimensions

    def search(
        self, query: str, embedding: Sequence[float], limit: int = 8,
        document_type: str | None = None, *, min_similarity: float = 0.0,
        skill_vector_lexical: bool = False,
    ) -> list[NativeHit]:
        if len(embedding) != self.dimensions:
            raise ValueError("query embedding dimension does not match the index profile")
        keyword_tsquery, keyword_enabled = keyword_query(query)
        term_groups = keyword_term_groups(query)
        params = {"profile_id": self.profile_id, "keyword": keyword_tsquery,
                  "keyword_enabled": keyword_enabled,
                  "keyword_term_pairs": keyword_term_pairs(query),
                  "group_count": len(term_groups),
                  "exact_title": "".join(keyword_query_tokens(query)),
                  "vector": vector_literal(embedding), "channel_limit": max(limit * 8, 40),
                  "document_type": document_type, "min_similarity": float(min_similarity),
                  "skill_vector_lexical": skill_vector_lexical}
        # Each channel ranks chunks in SQL, one best chunk per document enters the
        # fusion, and RRF sums 1/(60+doc_rank). Approval/version predicates are
        # repeated in both channels so revoked content cannot leak through RRF.
        # The keyword channel is OR-based but requires token coverage: OR alone let
        # a filler word ("一张" in 请帮我订一张机票) surface unrelated recipes.
        # The vector channel keeps the cosine floor only; fake skill embeddings
        # still require a shared term because their cosine is not a semantic score.
        sql = text("""
            WITH active AS (
              SELECT g.id FROM index_generations g
              WHERE g.active = true AND g.status = 'ready' AND g.profile_id = :profile_id
            ), keyword_groups AS (
               -- "<group_id>\x1f<term>": one row per query term per alias.
               SELECT CAST(split_part(cell, chr(31), 1) AS integer) AS group_id,
                      split_part(cell, chr(31), 2) AS term
               FROM unnest(CAST(:keyword_term_pairs AS text[])) AS cell
            ), keyword_matched AS (
               SELECT c.id chunk_id, c.document_id, d.recipe_id, c.text excerpt, s.id source_id,
                     d.content_version,
                     ts_rank(c.search_tsv, to_tsquery('simple', :keyword)) rank_score,
                     -- Coverage counts each query term once, no matter how many of
                     -- its aliases appear in the chunk.
                     (SELECT count(DISTINCT g.group_id) FROM keyword_groups g
                       WHERE c.search_tsv @@ to_tsquery('simple', g.term)) covered,
                     -- A document whose title is the query names the dish; one that
                     -- only contains the term in its title is a variant.
                     CASE
                       WHEN lower(regexp_replace(coalesce(d.title, ''), '\\s+', '', 'g'))
                            = lower(:exact_title) THEN 2
                       WHEN EXISTS (
                         SELECT 1 FROM keyword_groups g
                          WHERE strpos(lower(coalesce(d.title, '')), g.term) > 0)
                         THEN 1
                       ELSE 0
                     END title_tier
              FROM knowledge_chunks c
              JOIN knowledge_documents d ON d.id = c.document_id
              JOIN knowledge_sources s ON s.id = d.source_id
              JOIN active a ON a.id = c.generation_id
                WHERE CAST(:keyword_enabled AS boolean)
                 AND d.published = true AND s.approval = 'approved'
                 AND c.content_version = d.content_version
                 AND c.search_tsv @@ to_tsquery('simple', :keyword)
                 AND (CAST(:document_type AS text) IS NULL OR d.document_type = :document_type)
            ), keyword AS (
               SELECT chunk_id, document_id, recipe_id, excerpt, source_id, content_version,
                     row_number() OVER (
                       -- ts_rank is nearly constant for short queries, so without the
                       -- title tiers the order degraded to document_id. A dish named by
                       -- the query must outrank a dish that merely mentions the term.
                       ORDER BY title_tier DESC, covered DESC, rank_score DESC,
                                document_id, chunk_id) rank
              FROM keyword_matched
              -- OR alone is too loose: require most query terms, and always accept
              -- a single-term query so short food names (虾/鱼/葱) still match.
              WHERE covered >= GREATEST(1, CEIL(:group_count * 0.5))
            ), vector AS (
               SELECT c.id chunk_id, c.document_id, d.recipe_id, c.text excerpt, s.id source_id,
                     d.content_version, row_number() OVER (
                       ORDER BY c.embedding_vector <=> CAST(:vector AS vector),
                                 c.document_id, c.id) rank
              FROM knowledge_chunks c
              JOIN knowledge_documents d ON d.id = c.document_id
              JOIN knowledge_sources s ON s.id = d.source_id
              JOIN active a ON a.id = c.generation_id
               WHERE d.published = true AND s.approval = 'approved'
                 AND (CAST(:document_type AS text) IS NULL OR d.document_type = :document_type)
                AND c.content_version = d.content_version AND c.embedding_vector IS NOT NULL
                AND 1 - (c.embedding_vector <=> CAST(:vector AS vector)) >= :min_similarity
                AND (
                  CAST(:document_type AS text) IS DISTINCT FROM 'cooking_skill'
                  OR NOT CAST(:skill_vector_lexical AS boolean)
                  OR c.search_tsv @@ to_tsquery('simple', :keyword)
                )
            ), keyword_best AS (
               SELECT DISTINCT ON (document_id) * FROM keyword ORDER BY document_id, rank
            ), vector_best AS (
               SELECT DISTINCT ON (document_id) * FROM vector ORDER BY document_id, rank
            ), keyword_docs AS (
               SELECT *, row_number() OVER (ORDER BY rank, document_id, chunk_id) doc_rank
               FROM keyword_best
            ), vector_docs AS (
               SELECT *, row_number() OVER (ORDER BY rank, document_id, chunk_id) doc_rank
               FROM vector_best
            ), candidates AS (
              SELECT *, 'keyword' channel, 1.0 / (60 + doc_rank) rrf
              FROM keyword_docs WHERE doc_rank <= :channel_limit
              UNION ALL
              SELECT *, 'vector' channel, 1.0 / (60 + doc_rank) rrf
              FROM vector_docs WHERE doc_rank <= :channel_limit
            ), evidence AS (
               SELECT *, sum(rrf) OVER (PARTITION BY document_id) score,
               row_number() OVER (
                        PARTITION BY document_id ORDER BY rank, channel, chunk_id) evidence_rank
              FROM candidates
            ), channels AS (
               SELECT document_id,
                      string_agg(DISTINCT channel, ',' ORDER BY channel) AS channels
               FROM candidates GROUP BY document_id
            )
            SELECT e.document_id, e.recipe_id, e.chunk_id, e.score, e.excerpt,
                   e.source_id, e.content_version, ch.channels,
                   -- Document-level cosine: RRF and channels are fused per document,
                   -- so the floor must judge the best block of that document rather
                   -- than the single block that won the evidence rank.
                   (SELECT max(1 - (near.embedding_vector <=> CAST(:vector AS vector)))
                      FROM knowledge_chunks near
                      JOIN active act ON act.id = near.generation_id
                     WHERE near.document_id = e.document_id
                       AND near.content_version = e.content_version
                       AND near.embedding_vector IS NOT NULL) AS similarity
            FROM evidence e
            JOIN channels ch ON ch.document_id = e.document_id
            LEFT JOIN knowledge_chunks v
              ON v.id = e.chunk_id AND v.embedding_vector IS NOT NULL
            WHERE e.evidence_rank = 1
            ORDER BY e.score DESC, e.document_id LIMIT :limit
        """)
        return [NativeHit(**dict(row._mapping)) for row in self.connection.execute(
            sql, {**params, "limit": min(100, max(1, limit))}
        ).all()]


def rrf_hits(keyword: Sequence[RetrievalHit], vector: Sequence[RetrievalHit], limit: int = 8):
    """Small pure helper shared by SQL integration tests and diagnostics."""
    return reciprocal_rank_fusion((keyword, vector))[:limit]
