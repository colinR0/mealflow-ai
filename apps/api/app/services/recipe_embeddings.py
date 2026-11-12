"""Offline semantic embeddings for the homemade recipe-chunk pilot.

The official scorer loads Qwen3-Embedding-0.6B from a local directory. A missing
model stops the run. Tests inject another embedder; that path must not be
reported as semantic quality.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from app.services.hybrid_retrieval import cosine

MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
DIMENSIONS = 1024
INSTRUCTION_VERSION = "recipe-query-zh-v1"
QUERY_TASK = "根据用户的做菜问题，检索能够回答该问题的菜谱片段"
HIT_K = 5
MRR_K = 10


def query_text(query: str) -> str:
    return f"Instruct: {QUERY_TASK}\nQuery:{query}"


def embedding_profile() -> dict[str, Any]:
    return {
        "model_name": MODEL_NAME,
        "dimensions": DIMENSIONS,
        "instruction_version": INSTRUCTION_VERSION,
        "query_task": QUERY_TASK,
        "document_instruction": "",
    }


class RecipeEmbedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


def require_model_dir(model_dir: Path) -> Path:
    if not (model_dir / "config.json").is_file():
        raise FileNotFoundError(f"嵌入模型不存在：{model_dir}")
    return model_dir


def _last_token_pool(hidden: Any, attention_mask: Any) -> Any:
    """Qwen3-Embedding reads the last real token, so batches pad on the left."""
    import torch

    if bool(attention_mask[:, -1].sum() == attention_mask.shape[0]):
        return hidden[:, -1]
    positions = attention_mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), positions]


class QwenRecipeEmbedder:
    """Local Qwen3-Embedding-0.6B. Documents stay raw; queries get the fixed task."""

    def __init__(self, model_dir: Path) -> None:
        import torch
        from torch.nn import functional as functional
        from transformers import AutoModel, AutoTokenizer

        self.profile = embedding_profile()
        self._torch = torch
        self._normalize = functional.normalize
        self._tokenizer = AutoTokenizer.from_pretrained(
            require_model_dir(model_dir), padding_side="left"
        )
        # CPU 试点用 float32。配置里的 bfloat16 在这台机器上没有对应的快速内核。
        self._model = AutoModel.from_pretrained(model_dir, dtype=torch.float32)
        self._model.eval()
        hidden = int(getattr(self._model.config, "hidden_size", 0))
        if hidden != DIMENSIONS:
            raise ValueError(f"模型维度是 {hidden}，配置要求 {DIMENSIONS}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode([query_text(text) for text in texts])

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batch = self._tokenizer(
            texts, padding=True, truncation=True, max_length=1024, return_tensors="pt"
        )
        with self._torch.no_grad():
            hidden = self._model(**batch).last_hidden_state
            pooled = _last_token_pool(hidden, batch["attention_mask"])
            normal = self._normalize(pooled, p=2, dim=1)
        rows = normal.tolist()
        if any(len(row) != DIMENSIONS for row in rows):
            raise ValueError("嵌入结果维度与配置不一致")
        return rows


def rank_by_cosine(
    query_vector: list[float], chunks: list[dict[str, Any]], vectors: dict[str, list[float]]
) -> list[dict[str, Any]]:
    """Keep the closest chunk of each parent. A zero cosine does not count as a hit."""
    best: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        score = cosine(query_vector, vectors[chunk["chunk_id"]])
        if score <= 0:
            continue
        current = best.get(chunk["document_id"])
        if current is None or score > current["score"]:
            best[chunk["document_id"]] = {
                "document_id": chunk["document_id"],
                "name": chunk["name"],
                "section": chunk["section"],
                "chunk_id": chunk["chunk_id"],
                "score": round(score, 6),
            }
    return sorted(best.values(), key=lambda item: (-item["score"], item["document_id"]))


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def pilot_metrics(
    questions: list[dict[str, Any]], rankings: dict[str, list[str]]
) -> dict[str, Any]:
    """Micro recall and precision over answerable questions, same cutoffs as the string baseline."""
    hit_questions = 0
    reciprocal = 0.0
    relevant = 0
    found = 0
    returned = 0
    answerable = 0
    for question in questions:
        if not question["answerable"]:
            continue
        answerable += 1
        acceptable = set(question["acceptable_document_ids"])
        ordered = rankings[question["question_id"]]
        top = ordered[:HIT_K]
        hits = [doc_id for doc_id in top if doc_id in acceptable]
        relevant += len(acceptable)
        found += len(hits)
        returned += len(top)
        rank = next(
            (
                index
                for index, doc_id in enumerate(ordered[:MRR_K], 1)
                if doc_id in acceptable
            ),
            None,
        )
        if rank is not None and rank <= HIT_K:
            hit_questions += 1
        reciprocal += 1 / rank if rank is not None else 0.0
    return {
        "answerable": answerable,
        "hit_at_5": _rate(hit_questions, answerable),
        "recall_at_5": _rate(found, relevant),
        "precision_at_5": _rate(found, returned),
        "mrr_at_10": round(reciprocal / answerable, 4) if answerable else None,
    }
