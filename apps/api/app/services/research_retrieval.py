"""Read-only recipe retrieval; no answer generation or business side effects."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.cli.chunk_recipe_parents import (
    canonical_hash,
    render_header,
    render_ingredients,
    render_notes,
)
from app.services.hybrid_retrieval import RetrievalHit, reciprocal_rank_fusion
from app.services.research_index import (
    DIMENSIONS,
    INSTRUCTION_VERSION,
    MODEL_NAME,
    QUERY_PREFIX,
    BgeEmbedder,
    search_parents,
)

CANDIDATE_LIMIT = 20
RRF_K = 60
MODES = {"vector", "fts", "hybrid"}
PROFILE = {
    "model_name": MODEL_NAME,
    "dimension": DIMENSIONS,
    "query_prefix": QUERY_PREFIX,
    "instruction_version": INSTRUCTION_VERSION,
    "normalize": True,
}


class RetrievalError(RuntimeError):
    """Operational failure with a stable code; never represented as empty hits."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RecipeRetriever:
    """Reuse one instance to keep its BGE model loaded. The caller owns the engine."""

    def __init__(self, *, engine: Engine, model_dir: Path, batch_id: str) -> None:
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("batch_id 必须是非空字符串")
        self.engine = engine
        self.model_dir = Path(model_dir)
        self.batch_id = batch_id.strip()
        self._embedder: BgeEmbedder | None = None

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid") -> dict[str, Any]:
        """Return parent recipes, each with its best matching snippet per channel."""
        if mode not in MODES:
            raise ValueError("mode 必须是 vector、fts 或 hybrid")
        result = self._search(query, top_k, [mode])
        hits = result.pop("results")[mode]
        return {**result, "mode": mode, "status": "ok" if hits else "empty", "hits": hits}

    def search_all(self, query: str, top_k: int = 5) -> dict[str, Any]:
        """Compare three views using one embedding and one shared database snapshot."""
        result = self._search(query, top_k, ["vector", "fts", "hybrid"])
        results = result.pop("results")
        return {
            **result,
            "mode": "all",
            "statuses": {mode: "ok" if hits else "empty" for mode, hits in results.items()},
            **results,
        }

    def _batch(self, connection: Connection) -> dict[str, Any]:
        row = (
            connection.execute(
                text(
                    """
                SELECT id, corpus_sha256, chunks_sha256, policy_version, model_name,
                       dimension, query_prefix, instruction_version, normalize,
                       status, production_publishable
                FROM recipe_research.build_batches WHERE id = :batch
                """
                ),
                {"batch": self.batch_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise RetrievalError("index_config", f"批次不存在：{self.batch_id}")
        if row["status"] != "ready_for_evaluation" or row["production_publishable"]:
            raise RetrievalError(
                "index_config", "只允许检索未发布且 ready_for_evaluation 的研发批次"
            )
        if any(row[key] != value for key, value in PROFILE.items()):
            raise RetrievalError("index_config", "批次的模型、维度、查询前缀或归一化配置不匹配")
        # 内容版本重算必须使用建库时相同的渲染规则；新规则不能自动沿用。
        if row["policy_version"] not in {"recipe-structure-v1", "recipe-structure-research-v1"}:
            raise RetrievalError("index_config", f"不支持的切片规则：{row['policy_version']}")
        return dict(row)

    def get_detail(self, document_id: str, content_version: str) -> dict[str, Any]:
        """Read the parent only if this exact version belongs to the configured batch."""
        try:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                batch = self._batch(connection)
                rows = (
                    connection.execute(
                        text("""
                    SELECT DISTINCT p.document_id, p.name, p.source_asset_key,
                           p.source_sha256, p.source_record_key,
                           p.payload AS parent_payload, p.content_sha256 AS parent_sha256,
                           c.content_version
                    FROM recipe_research.chunks c
                    JOIN recipe_research.parents p ON p.document_id = c.document_id
                    WHERE c.batch_id = :batch AND c.document_id = :document
                """),
                        {"batch": self.batch_id, "document": document_id},
                    )
                    .mappings()
                    .all()
                )
                if not rows:
                    return {"status": "empty", "index": batch, "document": None}
                if {row["content_version"] for row in rows} != {content_version}:
                    raise RetrievalError("evidence_mismatch", "菜谱版本已变化，请重新搜索")
                self._verify_evidence({"detail": [dict(row) for row in rows]})
                row = rows[0]
                return {
                    "status": "ok",
                    "index": batch,
                    "document": {
                        "document_id": row["document_id"],
                        "content_version": content_version,
                        "parent_sha256": row["parent_sha256"],
                        "batch_id": self.batch_id,
                        "payload": row["parent_payload"],
                    },
                }
        except SQLAlchemyError as exc:
            raise RetrievalError("database_failed", "菜谱详情查询失败") from exc

    def _embed(self, query: str, timings: dict[str, float]) -> list[float]:
        started = time.perf_counter()
        if self._embedder is None:
            try:
                embedder = BgeEmbedder(self.model_dir)
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                # 转换为可辨认的模型配置错误，不能假装成检索无结果。
                raise RetrievalError("model_config", "BGE 加载失败，请检查依赖和本地模型") from exc
            local_profile = {**embedder.profile, "dimension": embedder.profile["dimensions"]}
            if any(local_profile[key] != value for key, value in PROFILE.items()):
                raise RetrievalError("model_config", "本地模型配置与固定索引不一致")
            self._embedder = embedder
        timings["model_load_ms"] = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        if self._embedder.count_tokens(QUERY_PREFIX + query) > int(
            self._embedder.profile["max_position_embeddings"]
        ):
            raise ValueError("问题超过 BGE token 上限，请缩短；不会静默截断")
        try:
            vector = self._embedder.embed_queries([query])[0]
        except (OSError, ValueError, RuntimeError) as exc:
            raise RetrievalError("embedding_failed", "问题向量化失败") from exc
        timings["query_embedding_ms"] = (time.perf_counter() - started) * 1000
        return vector

    @staticmethod
    def _verify_evidence(channels: dict[str, list[dict[str, Any]]]) -> None:
        versions: dict[str, str] = {}
        for rows in channels.values():
            for row in rows:
                parent = row["parent_payload"]
                try:
                    # 父表可被后续导入更新；不能把旧片段与新父菜谱拼成一份证据。
                    keys = (
                        "document_id",
                        "name",
                        "source_asset_key",
                        "source_sha256",
                        "source_record_key",
                    )
                    same_source = all(row[key] == parent.get(key) for key in keys)
                    same_parent = canonical_hash(parent) == row["parent_sha256"]
                    version = canonical_hash(
                        {
                            "header": render_header(parent),
                            "ingredients": render_ingredients(parent),
                            "notes": render_notes(parent),
                            "steps": [
                                f"{step['ordinal']}. {step['text'].strip()}"
                                for step in parent["steps"]
                            ],
                        }
                    )
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    raise RetrievalError("evidence_mismatch", "父菜谱无法按建库规则解析") from exc
                document_id = row["document_id"]
                if (
                    not same_source
                    or not same_parent
                    or version != row["content_version"]
                    or versions.get(document_id, version) != version
                ):
                    raise RetrievalError(
                        "evidence_mismatch", f"父菜谱与片段版本不一致：{document_id}"
                    )
                versions[document_id] = version

    @staticmethod
    def _results(
        channels: dict[str, list[dict[str, Any]]], modes: list[str], top_k: int
    ) -> dict[str, list[dict[str, Any]]]:
        evidence: dict[str, dict[str, dict[str, Any]]] = {}
        ranked_lists = []
        for channel, rows in channels.items():
            ranked = []
            score_key = "cosine" if channel == "vector" else "fts_rank"
            for rank, row in enumerate(rows, 1):
                document_id = row["document_id"]
                score = float(row[score_key])
                evidence.setdefault(document_id, {})[channel] = {
                    "rank": rank,
                    "score": score,
                    "score_type": "cosine" if channel == "vector" else "ts_rank",
                    **{
                        key: row[key]
                        for key in (
                            "chunk_id",
                            "section",
                            "step_ordinals",
                            "text",
                            "content_version",
                        )
                    },
                }
                # RRF 中的 recipe_id 是通用父级键；这里传研发 document_id，不是业务表外键。
                ranked.append(RetrievalHit(document_id, row["chunk_id"], rank, score, row["text"]))
            ranked_lists.append(ranked)
        fusion = reciprocal_rank_fusion(ranked_lists, k=RRF_K) if "hybrid" in modes else []
        parents = {row["document_id"]: row for rows in channels.values() for row in rows}
        results = {}
        for mode in modes:
            ordering = (
                fusion
                if mode == "hybrid"
                else [(row["document_id"], None) for row in channels[mode]]
            )
            hits = []
            for document_id, rrf_score in ordering[:top_k]:
                row = parents[document_id]
                # 单路结果仅显示本通道证据；混合结果保留两路，各路最多一个最好片段。
                matched = (
                    evidence[document_id]
                    if mode == "hybrid"
                    else {mode: evidence[document_id][mode]}
                )
                primary = min(
                    matched, key=lambda name: (matched[name]["rank"], 0 if name == "vector" else 1)
                )
                parent = row["parent_payload"]
                hits.append(
                    {
                        "rank": len(hits) + 1,
                        **{
                            key: row[key]
                            for key in (
                                "document_id",
                                "name",
                                "source_asset_key",
                                "source_sha256",
                                "source_record_key",
                                "content_version",
                                "policy_version",
                                "batch_id",
                            )
                        },
                        "source_kind": parent.get("source_kind"),
                        "source_version": parent.get("source_version"),
                        "parent_sha256": row["parent_sha256"],
                        "snippet": matched[primary],
                        "channels": matched,
                        "rrf_score": rrf_score,
                    }
                )
            results[mode] = hits
        return results

    def _search(self, query: str, top_k: int, modes: list[str]) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串")
        query = query.strip()
        if len(query) > 500:
            raise ValueError("query 最多 500 个字符")
        if type(top_k) is not int or not 1 <= top_k <= CANDIDATE_LIMIT:
            raise ValueError("top_k 必须是 1 至 20 的整数")
        started = time.perf_counter()
        timings = {"model_load_ms": 0.0, "query_embedding_ms": 0.0}
        use_vector = any(mode in {"vector", "hybrid"} for mode in modes)
        use_fts = any(mode in {"fts", "hybrid"} for mode in modes)
        try:
            with self.engine.begin() as connection:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                batch = self._batch(connection)
            # 模型加载与推理可能较慢，先结束数据库事务；FTS 完全跳过这个分支。
            vector = self._embed(query, timings) if use_vector else None
            channels = {}
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                if self._batch(connection) != batch:
                    raise RetrievalError("index_config", "检索期间批次配置发生变化，请重试")
                for channel, enabled in (("vector", use_vector), ("fts", use_fts)):
                    if not enabled:
                        continue
                    channel_started = time.perf_counter()
                    channels[channel] = search_parents(
                        connection,
                        batch_id=self.batch_id,
                        query=query,
                        query_vector=vector if channel == "vector" else None,
                        limit=CANDIDATE_LIMIT,
                        mode=channel,
                        include_payload=True,
                    )
                    timings[f"{channel}_query_ms"] = (time.perf_counter() - channel_started) * 1000
                self._verify_evidence(channels)
        except SQLAlchemyError as exc:
            # 不向终端输出驱动异常中的连接信息、SQL 参数或数据库凭据。
            raise RetrievalError(
                "database_failed", "数据库连接或查询失败，请检查数据库日志"
            ) from exc
        fusion_started = time.perf_counter()
        results = self._results(channels, modes, top_k)
        timings["result_assembly_ms"] = (time.perf_counter() - fusion_started) * 1000
        timings["total_ms"] = (time.perf_counter() - started) * 1000
        return {
            "query": query,
            "top_k": top_k,
            "index": batch,
            "candidate_limit_per_channel": CANDIDATE_LIMIT,
            "chunk_limit_per_channel": CANDIDATE_LIMIT * 8,
            "candidate_counts": {name: len(rows) for name, rows in channels.items()},
            "rrf_k": RRF_K if "hybrid" in modes else None,
            "timing_ms": {key: round(value, 2) for key, value in timings.items()},
            "results": results,
        }
