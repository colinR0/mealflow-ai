"""Create traceable recipe retrieval chunks from reviewed parent records.

Run from apps/api:
  python -m app.cli.chunk_recipe_parents --input output/prechunk-audit-v1/ready_for_chunking.jsonl

This is an offline preparation step. It does not embed, index, publish, or use
household inventory/profile data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.cli.register_recipe_sources import ROOT, fingerprint
from app.db.seed import INGREDIENTS

POLICY_VERSION = "recipe-structure-v1"
DEFAULT_INPUT = ROOT / "output/prechunk-audit-v1/ready_for_chunking.jsonl"
DEFAULT_OUTPUT = ROOT / "output/recipe-chunks-v1"
INGREDIENT_NAMES = {key: name for key, name, *_ in INGREDIENTS}


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ingredient_label(item: dict[str, Any]) -> str:
    key = item.get("key")
    if key and key not in INGREDIENT_NAMES:
        raise ValueError(f"食材键缺少中文名称映射：{key}")
    return INGREDIENT_NAMES[key] if key else str(item["name"]).strip()


def render_ingredients(parent: dict[str, Any]) -> list[str]:
    lines = []
    for item in parent["ingredients"]:
        name = ingredient_label(item)
        quantity = item.get("quantity")
        unit = item.get("unit")
        amount = f" {quantity:g}{unit}" if isinstance(quantity, (int, float)) and unit else ""
        optional = "（可选）" if item.get("optional") else ""
        lines.append(f"- {name}{amount}{optional}")
    return lines


def render_header(parent: dict[str, Any]) -> list[str]:
    fields = parent["fields"]
    lines = [f"菜名：{parent['name']}"]
    if fields.get("category"):
        lines.append(f"类别：{fields['category']}")
    if fields.get("servings"):
        lines.append(f"基准份数：{fields['servings']}人份")
    duration = fields.get("total_minutes") or fields.get("duration_minutes")
    if duration:
        lines.append(f"总用时：约{duration}分钟")
    if fields.get("equipment"):
        lines.append(f"设备：{fields['equipment']}")
    return lines


def render_notes(parent: dict[str, Any]) -> list[str]:
    fields = parent["fields"]
    lines = []
    if fields.get("completion_criteria"):
        lines.append(f"完成判断：{fields['completion_criteria']}")
    if fields.get("substitution_note"):
        lines.append(f"替换限制：{fields['substitution_note']}")
    allergens = fields.get("allergens")
    if allergens:
        lines.append("原文过敏原标注：" + "、".join(allergens))
    elif fields.get("allergen_note"):
        lines.append(f"原文过敏原提示（未经结构化核验）：{fields['allergen_note']}")
    return lines


def make_chunk(
    parent: dict[str, Any],
    *,
    content_version: str,
    section: str,
    text: str,
    ordinals: list[int],
    policy_version: str,
) -> dict[str, Any]:
    identity = [parent["document_id"], content_version, policy_version, section, ordinals]
    return {
        "chunk_id": canonical_hash(identity),
        "document_id": parent["document_id"],
        "name": parent["name"],
        "content_version": content_version,
        "policy_version": policy_version,
        "source_asset_key": parent["source_asset_key"],
        "source_record_key": parent.get("source_record_key"),
        "source_kind": parent.get("source_kind"),
        "source_version": parent.get("source_version"),
        "source_sha256": parent["source_sha256"],
        "parsed_sha256": parent.get("parsed_sha256"),
        "parser_version": parent.get("parser_version"),
        "audit_rule_version": parent.get("rule_version"),
        "section": section,
        "step_ordinals": ordinals,
        "text": text,
        "char_count": len(text),
    }


def split_parent(
    parent: dict[str, Any], *, max_chars: int, policy_version: str
) -> list[dict[str, Any]]:
    if (
        parent.get("status") != "loaded"
        or not parent.get("capabilities", {}).get("chunkable")
        or not parent.get("capabilities", {}).get("publishable_candidate")
    ):
        raise ValueError("父菜谱未通过切分输入筛选")
    steps = parent["steps"]
    ordinals = [step["ordinal"] for step in steps]
    if ordinals != list(range(1, len(steps) + 1)):
        raise ValueError("步骤编号不连续")

    header = render_header(parent)
    ingredient_lines = render_ingredients(parent)
    if not ingredient_lines:
        raise ValueError("没有可用食材")
    notes = render_notes(parent)
    step_lines = [f"{step['ordinal']}. {step['text'].strip()}" for step in steps]
    content_version = canonical_hash(
        {"header": header, "ingredients": ingredient_lines, "notes": notes, "steps": step_lines}
    )
    full = "\n".join([*header, "食材：", *ingredient_lines, "做法：", *step_lines, *notes])
    if len(full) <= max_chars:
        return [
            make_chunk(
                parent,
                content_version=content_version,
                section="full",
                text=full,
                ordinals=ordinals,
                policy_version=policy_version,
            )
        ]

    overview = "\n".join([*header, "食材：", *ingredient_lines, *notes])
    if len(overview) > max_chars:
        raise ValueError(f"食材及说明块超过 {max_chars} 字；需按字段边界进一步设计")
    chunks = [
        make_chunk(
            parent,
            content_version=content_version,
            section="overview",
            text=overview,
            ordinals=[],
            policy_version=policy_version,
        )
    ]
    context = f"菜名：{parent['name']}\n主要食材：" + "、".join(
        ingredient_label(item) for item in parent["ingredients"]
    )
    current: list[str] = []
    current_ordinals: list[int] = []
    for ordinal, step_line in zip(ordinals, step_lines, strict=True):
        candidate = "\n".join([context, "做法：", *current, step_line])
        if len(candidate) > max_chars and current:
            chunks.append(
                make_chunk(
                    parent,
                    content_version=content_version,
                    section="steps",
                    text="\n".join([context, "做法：", *current]),
                    ordinals=current_ordinals,
                    policy_version=policy_version,
                )
            )
            current, current_ordinals = [], []
            candidate = "\n".join([context, "做法：", step_line])
        if len(candidate) > max_chars:
            raise ValueError(f"步骤 {ordinal} 超过 {max_chars} 字；不能截断单个步骤")
        current.append(step_line)
        current_ordinals.append(ordinal)
    if current:
        chunks.append(
            make_chunk(
                parent,
                content_version=content_version,
                section="steps",
                text="\n".join([context, "做法：", *current]),
                ordinals=current_ordinals,
                policy_version=policy_version,
            )
        )
    recovered = [ordinal for chunk in chunks for ordinal in chunk["step_ordinals"]]
    if recovered != ordinals:
        raise ValueError("切分后步骤覆盖不完整")
    return chunks


def build_corpus(
    input_path: Path, output_dir: Path, *, max_chars: int, policy_version: str
) -> dict[str, Any]:
    input_hash, _ = fingerprint(input_path)
    parent_count = 0
    chunk_count = 0
    sections: Counter[str] = Counter()
    lengths: list[int] = []
    seen_ids: set[str] = set()
    problems: list[dict[str, str]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".tmp-chunks-") as tmp:
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
                    doc_id = parent["document_id"]
                    if doc_id in seen_ids:
                        raise ValueError(f"重复父文档 ID：{doc_id}")
                    seen_ids.add(doc_id)
                    chunks = split_parent(
                        parent, max_chars=max_chars, policy_version=policy_version
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    problems.append({"line": str(line_number), "error": str(exc)})
                    continue
                for chunk in chunks:
                    target.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    chunk_count += 1
                    sections[chunk["section"]] += 1
                    lengths.append(chunk["char_count"])

        chunks_hash, _ = fingerprint(chunk_path)
        summary = {
            "policy_version": policy_version,
            "input_sha256": input_hash,
            "chunks_sha256": chunks_hash,
            "parent_count": parent_count,
            "parents_chunked": parent_count - len(problems),
            "chunk_count": chunk_count,
            "section_counts": dict(sorted(sections.items())),
            "min_chars": min(lengths, default=0),
            "max_chars_observed": max(lengths, default=0),
            "max_chars_limit": max_chars,
            "error_count": len(problems),
            "embedding_performed": False,
            "database_write_performed": False,
            "production_publishable": False,
        }
        manifest = {
            "input_path": str(input_path),
            "input_sha256": input_hash,
            "chunks_sha256": chunks_hash,
            "policy_version": policy_version,
            "max_chars": max_chars,
            "parent_count": parent_count,
            "chunk_count": chunk_count,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-chars", type=int, default=450)
    parser.add_argument("--policy-version", default=POLICY_VERSION)
    args = parser.parse_args(argv)
    if args.max_chars < 200:
        parser.error("--max-chars must be at least 200")
    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    try:
        summary = build_corpus(
            input_path, output_dir, max_chars=args.max_chars, policy_version=args.policy_version
        )
    except (OSError, ValueError) as exc:
        print(f"recipe chunking failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
