"""Local BGE index for the research schema only.

Queries use the model prefix. Documents stay raw. A missing model or a failed
batch stops the command instead of returning an empty semantic result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import make_url

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
DIMENSIONS = 512
INSTRUCTION_VERSION = "bge-small-zh-v1.5-query-prefix"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
ALLOWED_DATABASE = "mealflow_s7"
HIT_K = 5
MRR_K = 10


def require_model_dir(model_dir: Path) -> Path:
    if not (model_dir / "config.json").is_file() or not (model_dir / "model.safetensors").is_file():
        raise FileNotFoundError(f"嵌入模型不存在：{model_dir}")
    return model_dir


def model_profile(model_dir: Path) -> dict[str, Any]:
    config = json.loads((require_model_dir(model_dir) / "config.json").read_text(encoding="utf-8"))
    hidden = int(config.get("hidden_size") or 0)
    positions = int(config.get("max_position_embeddings") or 0)
    if hidden != DIMENSIONS or positions < DIMENSIONS:
        raise ValueError(f"模型配置不是 512 维 BGE：hidden={hidden} positions={positions}")
    return {
        "model_name": MODEL_NAME,
        "model_dir": "models/bge-small-zh-v1.5",
        "dimensions": DIMENSIONS,
        "max_position_embeddings": positions,
        "vocab_size": config.get("vocab_size"),
        "query_prefix": QUERY_PREFIX,
        "instruction_version": INSTRUCTION_VERSION,
        "document_instruction": "",
        "normalize": True,
    }


def require_research_database(url: str) -> str:
    name = make_url(url).database
    if name != ALLOWED_DATABASE:
        raise RuntimeError(f"当前数据库是 {name}，研发索引入库只允许 {ALLOWED_DATABASE}")
    return url


def validate_vector(values: list[float], dimensions: int = DIMENSIONS) -> None:
    if len(values) != dimensions:
        raise ValueError(f"向量维度是 {len(values)}，要求 {dimensions}")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("向量含有非有限值")
    if not any(value != 0 for value in values):
        raise ValueError("零向量")


def vector_literal(values: list[float]) -> str:
    validate_vector(values)
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def fts_text(text: str) -> str:
    import jieba

    return " ".join(token.strip() for token in jieba.lcut(text) if token.strip())


def backup_database(url: str, destination: Path) -> None:
    """Write a custom-format dump. The caller must already have confirmed the database name."""
    parsed = make_url(require_research_database(url))
    dump = os.environ.get("PG_DUMP") or "pg_dump"
    destination.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    command = [
        dump,
        "-h",
        parsed.host or "127.0.0.1",
        "-p",
        str(parsed.port or 5432),
        "-U",
        parsed.username or "mealflow",
        "-d",
        parsed.database or ALLOWED_DATABASE,
        "-Fc",
        "-f",
        str(destination),
    ]
    subprocess.run(command, check=True, env=env)


def make_engine(url: str) -> Engine:
    return create_engine(require_research_database(url), pool_pre_ping=True)


def scalar(connection: Connection, sql: str, params: dict[str, Any] | None = None) -> Any:
    return connection.execute(text(sql), params or {}).scalar_one()


class BgeEmbedder:
    """CLS pooling, L2 normalization, and no silent truncation."""

    def __init__(self, model_dir: Path) -> None:
        import torch
        from torch.nn import functional as functional
        from transformers import AutoModel, AutoTokenizer

        self.profile = model_profile(model_dir)
        self._torch = torch
        self._normalize = functional.normalize
        self._max_tokens = int(self.profile["max_position_embeddings"])
        self._tokenizer = AutoTokenizer.from_pretrained(require_model_dir(model_dir))
        self._model = AutoModel.from_pretrained(model_dir, dtype=torch.float32)
        self._model.eval()
        hidden = int(getattr(self._model.config, "hidden_size", 0))
        if hidden != DIMENSIONS:
            raise ValueError(f"模型维度是 {hidden}，配置要求 {DIMENSIONS}")

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=True, truncation=False))

    def _encode(self, texts: list[str], *, prefix: str) -> list[list[float]]:
        if not texts:
            return []
        tokenized = self._tokenizer(
            [prefix + text for text in texts],
            padding=True,
            truncation=False,
            return_tensors="pt",
        )
        width = int(tokenized["input_ids"].shape[1])
        if width > self._max_tokens:
            raise ValueError(f"输入有 {width} 个 token，超过 {self._max_tokens}，拒绝截断")
        with self._torch.no_grad():
            hidden = self._model(**tokenized).last_hidden_state[:, 0]
            normal = self._normalize(hidden, p=2, dim=1)
        vectors = normal.tolist()
        for vector in vectors:
            validate_vector(vector)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, prefix="")

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, prefix=QUERY_PREFIX)


def batched(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def token_counter(model_dir: Path) -> tuple[Any, int]:
    """Count tokens with the local BGE tokenizer. This does not load the weights."""
    profile = model_profile(model_dir)
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(require_model_dir(model_dir))
    limit = int(profile["max_position_embeddings"])

    def count(text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=True, truncation=False))

    return count, limit


def _parent_params(parent: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(parent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "document_id": parent["document_id"],
        "source_asset_key": parent["source_asset_key"],
        "source_sha256": parent["source_sha256"],
        "source_record_key": parent.get("source_record_key"),
        "name": parent["name"],
        "content_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "publishable": bool(parent.get("publishable")),
        "chunkable": bool((parent.get("capabilities") or {}).get("chunkable")),
        "payload": payload,
    }


def upsert_parents(connection: Connection, parents: list[dict[str, Any]]) -> None:
    statement = text(
        """
        INSERT INTO recipe_research.parents (
            document_id, source_asset_key, source_sha256, source_record_key, name,
            content_sha256, publishable, chunkable, payload, created_at, updated_at
        ) VALUES (
            :document_id, :source_asset_key, :source_sha256, :source_record_key, :name,
            :content_sha256, :publishable, :chunkable, CAST(:payload AS jsonb), now(), now()
        )
        ON CONFLICT (document_id) DO UPDATE SET
            source_asset_key = EXCLUDED.source_asset_key,
            source_sha256 = EXCLUDED.source_sha256,
            source_record_key = EXCLUDED.source_record_key,
            name = EXCLUDED.name,
            content_sha256 = EXCLUDED.content_sha256,
            publishable = EXCLUDED.publishable,
            chunkable = EXCLUDED.chunkable,
            payload = EXCLUDED.payload,
            updated_at = now()
        """
    )
    connection.execute(statement, [_parent_params(parent) for parent in parents])


def find_resumable_batch(
    connection: Connection, profile: dict[str, Any], corpus: str, chunks: str
) -> str | None:
    row = connection.execute(
        text(
            """
            SELECT id FROM recipe_research.build_batches
            WHERE corpus_sha256 = :corpus AND chunks_sha256 = :chunks
              AND model_name = :model AND instruction_version = :instruction
              AND dimension = :dimension AND status IN ('building', 'failed')
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {
            "corpus": corpus,
            "chunks": chunks,
            "model": profile["model_name"],
            "instruction": profile["instruction_version"],
            "dimension": profile["dimensions"],
        },
    ).first()
    return None if row is None else str(row[0])


def find_ready_batch(
    connection: Connection, profile: dict[str, Any], corpus: str, chunks: str
) -> str | None:
    row = connection.execute(
        text(
            """
            SELECT id FROM recipe_research.build_batches
            WHERE corpus_sha256 = :corpus AND chunks_sha256 = :chunks
              AND model_name = :model AND instruction_version = :instruction
              AND status = 'ready_for_evaluation'
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {
            "corpus": corpus,
            "chunks": chunks,
            "model": profile["model_name"],
            "instruction": profile["instruction_version"],
        },
    ).first()
    return None if row is None else str(row[0])


def insert_batch(
    connection: Connection,
    batch_id: str,
    profile: dict[str, Any],
    corpus: str,
    chunks: str,
    policy: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO recipe_research.build_batches (
                id, corpus_sha256, chunks_sha256, policy_version, model_name, dimension,
                query_prefix, instruction_version, normalize, status,
                production_publishable, created_at, updated_at
            ) VALUES (
                :id, :corpus, :chunks, :policy, :model, :dimension,
                :prefix, :instruction, true, 'building', false, now(), now()
            )
            """
        ),
        {
            "id": batch_id,
            "corpus": corpus,
            "chunks": chunks,
            "policy": policy,
            "model": profile["model_name"],
            "dimension": profile["dimensions"],
            "prefix": profile["query_prefix"],
            "instruction": profile["instruction_version"],
        },
    )


def mark_batch(
    connection: Connection,
    batch_id: str,
    *,
    status: str,
    error: str | None,
    counts: tuple[int, int],
) -> None:
    connection.execute(
        text(
            """
            UPDATE recipe_research.build_batches
            SET status = :status, error = :error, chunk_count = :chunks,
                parent_count = :parents, updated_at = now()
            WHERE id = :id AND production_publishable = false
            """
        ),
        {
            "id": batch_id,
            "status": status,
            "error": error,
            "chunks": counts[0],
            "parents": counts[1],
        },
    )


def insert_chunk_vectors(
    connection: Connection,
    batch_id: str,
    rows: list[dict[str, Any]],
    vectors: list[list[float]],
) -> None:
    statement = text(
        """
        INSERT INTO recipe_research.chunks (
            batch_id, chunk_id, document_id, content_version, section, step_ordinals,
            text, embedding, search_tsv, created_at
        ) VALUES (
            :batch_id, :chunk_id, :document_id, :content_version, :section,
            CAST(:step_ordinals AS jsonb), :text, CAST(:embedding AS vector),
            to_tsvector('simple', :fts_text), now()
        )
        ON CONFLICT (batch_id, chunk_id) DO NOTHING
        """
    )
    payload = []
    for row, vector in zip(rows, vectors, strict=True):
        payload.append(
            {
                "batch_id": batch_id,
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "content_version": row["content_version"],
                "section": row["section"],
                "step_ordinals": json.dumps(row["step_ordinals"], ensure_ascii=False),
                "text": row["text"],
                "embedding": vector_literal(vector),
                "fts_text": fts_text(row["text"]),
            }
        )
    connection.execute(statement, payload)


def existing_chunk_ids(connection: Connection, batch_id: str) -> set[str]:
    rows = connection.execute(
        text("SELECT chunk_id FROM recipe_research.chunks WHERE batch_id = :batch"),
        {"batch": batch_id},
    )
    return {str(row[0]) for row in rows}


def validate_batch(connection: Connection, batch_id: str, expected: int) -> None:
    row = connection.execute(
        text(
            """
            SELECT COUNT(*) AS n,
                   COUNT(embedding) AS embedded,
                   COUNT(*) FILTER (WHERE vector_dims(embedding) <> 512) AS bad_dim,
                   COUNT(*) FILTER (WHERE vector_norm(embedding) = 0) AS zeros
            FROM recipe_research.chunks
            WHERE batch_id = :batch
            """
        ),
        {"batch": batch_id},
    ).one()
    counts_match = int(row.n) == expected and int(row.embedded) == expected
    if not counts_match or int(row.bad_dim) or int(row.zeros):
        raise RuntimeError(
            f"批次校验失败：count={row.n} embedded={row.embedded} "
            f"bad_dim={row.bad_dim} zeros={row.zeros} expected={expected}"
        )


def _require_ready_batch(connection: Connection, batch_id: str) -> None:
    row = connection.execute(
        text(
            """
            SELECT status, production_publishable
            FROM recipe_research.build_batches WHERE id = :id
            """
        ),
        {"id": batch_id},
    ).first()
    if row is None:
        raise RuntimeError(f"构建批次不存在：{batch_id}")
    if row.status != "ready_for_evaluation":
        raise RuntimeError(f"构建批次状态是 {row.status}，不能检索")
    if row.production_publishable:
        raise RuntimeError("研发批次被标成正式发布，已停止检索")


def search_parents(
    connection: Connection,
    *,
    batch_id: str,
    query_vector: list[float],
    query: str,
    limit: int,
    mode: str,
) -> list[dict[str, Any]]:
    _require_ready_batch(connection, batch_id)
    validate_vector(query_vector)
    if mode not in {"vector", "fts"}:
        raise ValueError(f"未知检索方式：{mode}")
    order = "c.embedding <=> CAST(:vector AS vector)" if mode == "vector" else "fts_rank DESC"
    statement = text(
        f"""
        SELECT c.document_id, c.chunk_id, c.section, c.content_version, c.text,
               p.name, p.source_asset_key, p.source_sha256, p.source_record_key,
               b.policy_version, b.model_name, b.instruction_version,
               1 - (c.embedding <=> CAST(:vector AS vector)) AS cosine,
               ts_rank(c.search_tsv, plainto_tsquery('simple', :fts_query)) AS fts_rank
        FROM recipe_research.chunks c
        JOIN recipe_research.parents p ON p.document_id = c.document_id
        JOIN recipe_research.build_batches b ON b.id = c.batch_id
        WHERE c.batch_id = :batch
        ORDER BY {order}
        LIMIT :limit
        """
    )
    rows = connection.execute(
        statement,
        {
            "batch": batch_id,
            "vector": vector_literal(query_vector),
            "fts_query": fts_text(query),
            "limit": max(limit * 8, limit),
        },
    ).mappings()
    score_key = "cosine" if mode == "vector" else "fts_rank"
    best: dict[str, dict[str, Any]] = {}
    order_ids: list[str] = []
    for row in rows:
        item = dict(row)
        item["batch_id"] = batch_id
        document_id = str(item["document_id"])
        previous = best.get(document_id)
        if previous is None:
            best[document_id] = item
            order_ids.append(document_id)
        elif float(item[score_key]) > float(previous[score_key]):
            best[document_id] = item
    return [best[document_id] for document_id in order_ids][:limit]


def retrieval_metrics(
    questions: list[dict[str, Any]], rankings: dict[str, list[str]]
) -> dict[str, Any]:
    hit_questions = 0
    reciprocal = 0.0
    relevant = 0
    found = 0
    returned = 0
    answerable = 0
    violations = []
    false_recalls = []
    for question in questions:
        ordered = rankings[question["question_id"]]
        top = ordered[:HIT_K]
        banned = set(question.get("must_exclude_document_ids") or [])
        excluded = [document_id for document_id in top if document_id in banned]
        if excluded:
            violations.append({"question_id": question["question_id"], "document_ids": excluded})
        if not question["answerable"]:
            if top:
                false_recalls.append(
                    {"question_id": question["question_id"], "document_ids": top[:3]}
                )
            continue
        answerable += 1
        acceptable = set(question["acceptable_document_ids"])
        hits = [document_id for document_id in top if document_id in acceptable]
        relevant += len(acceptable)
        found += len(hits)
        returned += len(top)
        rank = next(
            (
                index
                for index, document_id in enumerate(ordered[:MRR_K], 1)
                if document_id in acceptable
            ),
            None,
        )
        if rank is not None and rank <= HIT_K:
            hit_questions += 1
        if not hits:
            false_recalls.append(
                {"question_id": question["question_id"], "top_document_ids": top[:3]}
            )
        reciprocal += 1 / rank if rank is not None else 0.0

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "answerable": answerable,
        "hit_at_5": rate(hit_questions, answerable),
        "recall_at_5": rate(found, relevant),
        "precision_at_5": rate(found, returned),
        "mrr_at_10": round(reciprocal / answerable, 4) if answerable else None,
        "exclusion_violations": violations,
        "false_recall_examples": false_recalls[:8],
        "allergen_hard_filter": False,
    }
