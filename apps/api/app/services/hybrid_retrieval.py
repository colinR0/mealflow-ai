"""小规模、可审计的混合检索核心；embedding 是显式标记的 fake，不代表质量评测。"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalHit:
    recipe_id: str
    chunk_id: str
    rank: int
    score: float
    text: str


def fake_embedding(text: str, dimensions: int = 64) -> list[float]:
    """确定性词袋哈希，仅供本地线路联调，不能声称是真实语义向量。"""
    if dimensions < 1:
        raise ValueError("dimensions 必须为正数")
    vector = [0.0] * dimensions
    for token in re.findall(r"[\w\u3400-\u9fff]+", text.lower()):
        index = sum((i + 1) * ord(char) for i, char in enumerate(token)) % dimensions
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("向量维度不匹配")
    if not all(math.isfinite(value) for value in (*left, *right)):
        raise ValueError("向量必须为有限数值")
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalHit]], *, k: int = 60
) -> list[tuple[str, float]]:
    """按父菜谱融合，各路同菜多块只保留最好名次，同分稳定按 ID 排序。"""
    if k < 1:
        raise ValueError("k 必须为正数")
    scores: dict[str, float] = {}
    for hits in ranked_lists:
        best_rank: dict[str, int] = {}
        for hit in hits:
            best_rank[hit.recipe_id] = min(best_rank.get(hit.recipe_id, hit.rank), hit.rank)
        for recipe_id, rank in best_rank.items():
            if rank < 1:
                raise ValueError("rank 从 1 开始")
            scores[recipe_id] = scores.get(recipe_id, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def evaluate_retrieval(cases: Sequence[dict[str, Any]], *, k: int = 5) -> dict[str, float]:
    """报告样本命中率及 MRR；不把演示语料结果当成客户效果。"""
    if not cases:
        return {"hit_at_k": 0.0, "mrr": 0.0, "queries": 0.0}
    hits = reciprocal_ranks = 0.0
    for case in cases:
        expected = set(case.get("relevant_recipe_ids", []))
        ranked = list(case.get("ranked_recipe_ids", []))
        first = next((index for index, item in enumerate(ranked, 1) if item in expected), None)
        hits += first is not None and first <= k
        reciprocal_ranks += 1 / first if first else 0.0
    return {
        "hit_at_k": hits / len(cases),
        "mrr": reciprocal_ranks / len(cases),
        "queries": float(len(cases)),
    }
