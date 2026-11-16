"""Split knowledge documents into token-bounded chunks for embedding.

Chunks are built from structured content when available so a recipe step or a
Markdown section is never cut in the middle. Every chunk repeats the document
title so keyword search can still hit a dish name from any block.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

TokenCounter = Callable[[str], int]

MAX_TOKENS = 480
OVERLAP_TOKENS = 60
_SENTENCES = re.compile(r"(?<=[。！？；!?;])|\n+")


def split_document(
    *,
    text: str,
    title: str | None,
    document_type: str,
    content: Any,
    count_tokens: TokenCounter,
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[str]:
    """Return chunks in source order, each within ``max_tokens``."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    title = (title or "").strip()
    units = (
        _recipe_units(content, text)
        if document_type == "recipe"
        else _skill_units(content, text)
    )
    return _pack(
        units,
        title=title,
        max_tokens=max_tokens,
        overlap_tokens=max(0, overlap_tokens),
        count_tokens=count_tokens,
    ) or [title]


def _recipe_units(content: Any, fallback: str) -> list[str]:
    if not isinstance(content, dict):
        return _sentences(fallback)
    names = [
        str(item.get("name") or "").strip()
        for item in content.get("ingredients") or []
        if str(item.get("name") or "").strip()
    ]
    steps = [
        str(step).strip()
        for step in content.get("steps") or []
        if str(step).strip()
    ]
    units: list[str] = []
    if names:
        units.append("食材：" + "、".join(dict.fromkeys(names)))
    units.extend(f"步骤{index}：{step}" for index, step in enumerate(steps, 1))
    return units or _sentences(fallback)


def _skill_units(content: Any, fallback: str) -> list[str]:
    markdown = content.get("markdown") if isinstance(content, dict) else None
    source = (markdown or fallback or "").strip()
    if not source:
        return []
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n|(?=^#{1,6}\s)", source, flags=re.MULTILINE)
        if block.strip()
    ]
    units: list[str] = []
    for block in blocks:
        units.extend(_sentences(block))
    return units


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCES.split(text or "") if part.strip()]


def _pack(
    units: Sequence[str],
    *,
    title: str,
    max_tokens: int,
    overlap_tokens: int,
    count_tokens: TokenCounter,
) -> list[str]:
    prefix = f"{title}\n" if title else ""
    budget = max_tokens - count_tokens(prefix)
    if budget < 1:
        raise ValueError("title leaves no room for chunk content")
    pieces: list[str] = []
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        if count_tokens(unit) <= budget:
            pieces.append(unit)
        else:
            pieces.extend(_hard_split(unit, budget=budget, count_tokens=count_tokens))

    def render(parts: Sequence[str]) -> str:
        return prefix + "\n".join(parts)

    def fits(parts: Sequence[str]) -> bool:
        return count_tokens(render(parts)) <= max_tokens

    chunks: list[str] = []
    current: list[str] = []
    for piece in pieces:
        if current and not fits([*current, piece]):
            chunks.append(render(current))
            last = current[-1]
            if overlap_tokens and count_tokens(last) <= overlap_tokens and fits([last, piece]):
                current = [last]
            else:
                current = []
        current.append(piece)
    if current:
        chunks.append(render(current))
    return chunks


def _hard_split(text: str, *, budget: int, count_tokens: TokenCounter) -> list[str]:
    parts: list[str] = []
    rest = text.strip()
    while rest:
        if count_tokens(rest) <= budget:
            parts.append(rest)
            break
        low, high = 1, len(rest)
        while low < high:
            middle = (low + high + 1) // 2
            if count_tokens(rest[:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        parts.append(rest[:low].strip())
        rest = rest[low:].lstrip()
    return [part for part in parts if part]