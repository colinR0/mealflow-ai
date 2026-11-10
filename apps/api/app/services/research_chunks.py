"""Research chunking for the combined parent corpus.

The official splitter still requires publishable_candidate. This entry accepts
research candidates and already reviewed parents, and it refuses to let the
tokenizer truncate a chunk.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.cli.chunk_recipe_parents import (
    canonical_hash,
    ingredient_label,
    make_chunk,
    render_header,
    render_ingredients,
    render_notes,
)
from app.cli.register_recipe_sources import fingerprint

POLICY_VERSION = "recipe-structure-research-v1"
_SENTENCE = re.compile(r"[^。！？；;\n]+[。！？；;\n]?")


class ChunkTooLong(ValueError):
    """This parent cannot be stored without cutting a sentence or a step."""


def split_sentences(text: str) -> list[str]:
    parts = [match.group(0) for match in _SENTENCE.finditer(text)]
    if "".join(parts) != text:
        return [text]
    return [part for part in parts if part.strip()]


def _fits(
    text: str, *, max_chars: int, max_tokens: int, count_tokens: Callable[[str], int]
) -> bool:
    return len(text) <= max_chars and count_tokens(text) <= max_tokens


def _require_research_parent(parent: dict[str, Any]) -> None:
    capabilities = parent.get("capabilities") or {}
    if parent.get("status") != "loaded" or not capabilities.get("chunkable"):
        raise ValueError("父菜谱未通过研发切分输入筛选")
    steps = parent["steps"]
    ordinals = [step["ordinal"] for step in steps]
    if ordinals != list(range(1, len(steps) + 1)):
        raise ValueError("步骤编号不连续")


def verify_step_coverage(parent: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    expected = {step["ordinal"]: step["text"].strip() for step in parent["steps"]}
    found: dict[int, list[tuple[int, str]]] = {ordinal: [] for ordinal in expected}
    for chunk in chunks:
        for span in chunk["step_spans"]:
            found[span["ordinal"]].append((span["part_index"], span["text"]))
    for ordinal, text in expected.items():
        parts = sorted(found[ordinal])
        indexes = [index for index, _piece in parts]
        if indexes != list(range(len(indexes))):
            raise ValueError(f"步骤 {ordinal} 片段序号不连续")
        if "".join(piece for _index, piece in parts) != text:
            raise ValueError(f"步骤 {ordinal} 覆盖不完整或重复")


def _chunk(
    parent: dict[str, Any],
    *,
    content_version: str,
    policy_version: str,
    section: str,
    text: str,
    ordinals: list[int],
    spans: list[dict[str, Any]],
    piece_index: int,
) -> dict[str, Any]:
    chunk = make_chunk(
        parent,
        content_version=content_version,
        section=section,
        text=text,
        ordinals=ordinals,
        policy_version=policy_version,
    )
    identity = [
        parent["document_id"],
        content_version,
        policy_version,
        section,
        ordinals,
        [span["part_index"] for span in spans],
        piece_index,
    ]
    chunk["chunk_id"] = canonical_hash(identity)
    chunk["step_spans"] = spans
    return chunk


def _pack(
    pieces: list[str],
    *,
    prefix: list[str],
    max_chars: int,
    max_tokens: int,
    count_tokens: Callable[[str], int],
) -> list[str]:
    groups: list[str] = []
    current: list[str] = []

    def render(lines: list[str]) -> str:
        return "\n".join([*prefix, *lines])

    def fits(text: str) -> bool:
        return _fits(text, max_chars=max_chars, max_tokens=max_tokens, count_tokens=count_tokens)

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(render(current))
            current = []

    for piece in pieces:
        if fits(render([*current, piece])):
            current.append(piece)
            continue
        flush()
        if fits(render([piece])):
            current.append(piece)
            continue
        sentences = split_sentences(piece)
        if len(sentences) <= 1:
            raise ChunkTooLong("字段超过长度上限且无法按句子拆开")
        for sentence in sentences:
            if current and not fits(render([*current, sentence])):
                flush()
            if not fits(render([sentence])):
                raise ChunkTooLong("句子超过长度上限")
            current.append(sentence)
    flush()
    return groups


def _fit_context(
    parent: dict[str, Any],
    labels: list[str],
    *,
    max_chars: int,
    max_tokens: int,
    count_tokens: Callable[[str], int],
) -> str:
    name_line = f"菜名：{parent['name']}"
    if not _fits(name_line, max_chars=max_chars, max_tokens=max_tokens, count_tokens=count_tokens):
        raise ChunkTooLong("菜名超过长度上限")
    chosen: list[str] = []
    for label in labels:
        candidate = name_line + "\n主要食材：" + "、".join([*chosen, label])
        if not _fits(
            candidate, max_chars=max_chars, max_tokens=max_tokens, count_tokens=count_tokens
        ):
            break
        chosen.append(label)
    if not chosen:
        return name_line
    return name_line + "\n主要食材：" + "、".join(chosen)


def split_research_parent(
    parent: dict[str, Any],
    *,
    max_chars: int,
    max_tokens: int,
    count_tokens: Callable[[str], int],
    policy_version: str,
) -> list[dict[str, Any]]:
    _require_research_parent(parent)
    header = render_header(parent)
    ingredient_lines = render_ingredients(parent)
    if not ingredient_lines:
        raise ValueError("没有可用食材")
    notes = render_notes(parent)
    steps = parent["steps"]
    step_lines = [f"{step['ordinal']}. {step['text'].strip()}" for step in steps]
    content_version = canonical_hash(
        {"header": header, "ingredients": ingredient_lines, "notes": notes, "steps": step_lines}
    )
    limits = {"max_chars": max_chars, "max_tokens": max_tokens, "count_tokens": count_tokens}
    full = "\n".join([*header, "食材：", *ingredient_lines, "做法：", *step_lines, *notes])
    full_spans = [
        {"ordinal": step["ordinal"], "part_index": 0, "part_count": 1, "text": step["text"].strip()}
        for step in steps
    ]
    if _fits(full, **limits):
        chunks = [
            _chunk(
                parent,
                content_version=content_version,
                policy_version=policy_version,
                section="full",
                text=full,
                ordinals=[step["ordinal"] for step in steps],
                spans=full_spans,
                piece_index=0,
            )
        ]
        verify_step_coverage(parent, chunks)
        return chunks

    overview_texts = _pack(
        [*header[1:], "食材：", *ingredient_lines, *notes],
        prefix=[header[0]],
        **limits,
    )
    chunks = [
        _chunk(
            parent,
            content_version=content_version,
            policy_version=policy_version,
            section="overview",
            text=text,
            ordinals=[],
            spans=[],
            piece_index=index,
        )
        for index, text in enumerate(overview_texts)
    ]
    labels = [ingredient_label(item) for item in parent["ingredients"]]
    context = _fit_context(parent, labels, **limits)
    current: list[str] = []
    current_ordinals: list[int] = []
    current_spans: list[dict[str, Any]] = []

    def emit() -> None:
        nonlocal current, current_ordinals, current_spans
        if not current:
            return
        chunks.append(
            _chunk(
                parent,
                content_version=content_version,
                policy_version=policy_version,
                section="steps",
                text="\n".join([context, "做法：", *current]),
                ordinals=list(current_ordinals),
                spans=list(current_spans),
                piece_index=0,
            )
        )
        current, current_ordinals, current_spans = [], [], []

    def add_piece(line: str, span: dict[str, Any]) -> None:
        nonlocal current, current_ordinals, current_spans
        candidate = "\n".join([context, "做法：", *current, line])
        if current and not _fits(candidate, **limits):
            emit()
        if not _fits("\n".join([context, "做法：", line]), **limits):
            raise ChunkTooLong(f"步骤 {span['ordinal']} 超过长度上限")
        current.append(line)
        if span["ordinal"] not in current_ordinals:
            current_ordinals.append(span["ordinal"])
        current_spans.append(span)

    for step, step_line in zip(steps, step_lines, strict=True):
        span = {
            "ordinal": step["ordinal"],
            "part_index": 0,
            "part_count": 1,
            "text": step["text"].strip(),
        }
        if _fits("\n".join([context, "做法：", *current, step_line]), **limits):
            add_piece(step_line, span)
            continue
        if current:
            emit()
        if _fits("\n".join([context, "做法：", step_line]), **limits):
            add_piece(step_line, span)
            continue
        sentences = split_sentences(step["text"].strip())
        if len(sentences) <= 1 or "".join(sentences) != step["text"].strip():
            raise ChunkTooLong(f"步骤 {step['ordinal']} 超过长度上限")
        for part_index, sentence in enumerate(sentences):
            add_piece(
                f"{step['ordinal']}. {sentence}",
                {
                    "ordinal": step["ordinal"],
                    "part_index": part_index,
                    "part_count": len(sentences),
                    "text": sentence,
                },
            )
    emit()
    verify_step_coverage(parent, chunks)
    return chunks


def build_research_corpus(
    input_path: Path,
    output_dir: Path,
    *,
    max_chars: int,
    max_tokens: int,
    count_tokens: Callable[[str], int],
    policy_version: str,
) -> dict[str, Any]:
    input_hash, _size = fingerprint(input_path)
    parent_count = 0
    sections: Counter[str] = Counter()
    lengths: list[int] = []
    token_lengths: list[int] = []
    seen_ids: set[str] = set()
    problems: list[dict[str, str]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".tmp-research-chunks-") as tmp:
        tmp_dir = Path(tmp)
        chunk_path = tmp_dir / "chunks.jsonl"
        with input_path.open(encoding="utf-8") as source, chunk_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as target:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                parent_count += 1
                try:
                    parent = json.loads(line)
                    document_id = parent["document_id"]
                    if document_id in seen_ids:
                        raise ValueError(f"重复父文档 ID：{document_id}")
                    seen_ids.add(document_id)
                    chunks = split_research_parent(
                        parent,
                        max_chars=max_chars,
                        max_tokens=max_tokens,
                        count_tokens=count_tokens,
                        policy_version=policy_version,
                    )
                except ChunkTooLong as exc:
                    problems.append(
                        {
                            "line": str(line_number),
                            "document_id": str(parent.get("document_id", "")),
                            "name": str(parent.get("name", "")),
                            "error": str(exc),
                        }
                    )
                    continue
                for chunk in chunks:
                    target.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    sections[chunk["section"]] += 1
                    lengths.append(chunk["char_count"])
                    token_lengths.append(count_tokens(chunk["text"]))
        chunks_hash, _size = fingerprint(chunk_path)
        summary = {
            "policy_version": policy_version,
            "input_sha256": input_hash,
            "chunks_sha256": chunks_hash,
            "parent_count": parent_count,
            "parents_chunked": parent_count - len(problems),
            "chunk_count": sum(sections.values()),
            "section_counts": dict(sorted(sections.items())),
            "min_chars": min(lengths, default=0),
            "max_chars_observed": max(lengths, default=0),
            "max_chars_limit": max_chars,
            "max_tokens_observed": max(token_lengths, default=0),
            "max_tokens_limit": max_tokens,
            "error_count": len(problems),
            "embedding_performed": False,
            "database_write_performed": False,
            "production_publishable": False,
        }
        manifest = {
            "input_sha256": input_hash,
            "chunks_sha256": chunks_hash,
            "policy_version": policy_version,
            "max_chars": max_chars,
            "max_tokens": max_tokens,
            "parent_count": parent_count,
            "chunk_count": summary["chunk_count"],
        }
        (tmp_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (tmp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (tmp_dir / "issues.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in problems),
            encoding="utf-8",
        )
        for name in ("chunks.jsonl", "issues.jsonl", "summary.json", "manifest.json"):
            os.replace(tmp_dir / name, output_dir / name)
    return summary
