"""Audit recipe sources into parent records before any chunking or publishing.

Read-only by default: consume a source-registry JSON report (or scan inputs),
verify digests, parse known formats, and write summary/parents/issues/review
artifacts atomically. Database writes require an explicit future switch and are
not performed by this first version.

Run from apps/api:
  python -m app.cli.audit_recipe_loading --registry-report ... --output-dir ...
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from app.cli.register_recipe_sources import ROOT, fingerprint, scan

RULE_VERSION = "prechunk-audit-v1"
PARSER_VERSION = "text-v2"
SAMPLE_MANIFEST = ROOT / "output/recipe-loading-v1/manifest.json"
PENDING_ROOT = ROOT / "data/sources/pending"
DEFAULT_PARSED_PDF_DIR = ROOT / "output/mineru-batch-v1"
DEFAULT_OUTPUT = ROOT / "output/prechunk-audit-v1"
SOURCE_JSON = "data/seed/recipes.json"
LEGACY_JSON = "apps/web/public/data/recipes_merged.json"

NUMBERED_STEP = re.compile(r"(?m)^(\d+)\.\s+(.+)$")
QUANTITY = re.compile(
    r"^(?P<name>.+?)\s+(?P<approx>约\s*)?(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|mL|g|L|个|条|根|瓣|勺|茶匙|汤匙)(?=[，、\s]|$)"
)
NONSTANDARD_QTY = re.compile(r"(适量|少许|\d+个/\d+个|\d+\s*勺)")

SAMPLE_FIELD_ORDER = (
    "菜名",
    "类别",
    "基准份数",
    "准备时间",
    "烹饪时间",
    "预计总时间",
    "设备",
    "过敏原与核对",
    "完成判断",
    "可选替换及限制",
    "来源",
    "使用说明",
)


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def positive_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return False
    return number.is_finite() and number > 0


def nonnegative_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return False
    return number.is_finite() and number >= 0


def web_url(value: object) -> bool:
    if not nonempty(value):
        return False
    try:
        parsed = urlparse(str(value))
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def name_key(value: str) -> str:
    return re.sub(r"[\s（）()·?,，、]+", "", value).casefold()


def issue(
    code: str,
    severity: str,
    *,
    source_key: str,
    record_key: str | None = None,
    field_name: str | None = None,
    excerpt: str | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "source_key": source_key,
        "source_record_key": record_key,
        "field": field_name,
        "excerpt": excerpt,
        "suggestion": suggestion,
    }


def document_id(source_key: str, record_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"mealflow-recipe:{source_key}#{record_key}"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")

@dataclass
class AuditContext:
    registry: dict[str, dict[str, Any]]
    rule_version: str
    parser_version: str
    parsed_pdf_dir: Path
    force: bool
    previous_state: dict[str, Any] = field(default_factory=dict)
    cached_parents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    file_results: list[dict[str, Any]] = field(default_factory=list)
    identity_conflicts: list[dict[str, Any]] = field(default_factory=list)
    processed_keys: set[str] = field(default_factory=set)
    skipped_keys: set[str] = field(default_factory=set)


def load_registry(path: Path | None, inputs: list[Path] | None) -> dict[str, dict[str, Any]]:
    if path is None:
        from app.cli.register_recipe_sources import default_inputs

        scan_inputs = inputs if inputs is not None else default_inputs()
        report = scan(scan_inputs, commit=False)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload.get("records"), list):
            raise ValueError("来源登记报告缺少 records 数组")
        report = payload
    registry: dict[str, dict[str, Any]] = {}
    for record in report["records"]:
        key = record.get("source_key")
        if not isinstance(key, str) or not key:
            raise ValueError("来源登记报告存在无效 source_key")
        if key in registry:
            raise ValueError(f"来源登记报告存在重复路径：{key}")
        if record.get("result") == "error":
            registry[key] = {"error": record.get("error") or "登记失败", **record}
            continue
        digest = record.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"来源登记报告缺少有效摘要：{key}")
        registry[key] = record
    return registry


def verify_source(
    source_key: str,
    registry: dict[str, dict[str, Any]],
    expected_hash: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    problems: list[dict[str, Any]] = []
    path = ROOT / source_key
    if not path.is_file():
        problems.append(
            issue(
                "SOURCE_MISSING_FILE",
                "error",
                source_key=source_key,
                suggestion="确认原始来源仍存在于配置目录",
            )
        )
        return "", problems
    try:
        actual_hash, _size = fingerprint(path)
    except (OSError, ValueError) as exc:
        problems.append(
            issue(
                "SOURCE_READ_FAILED",
                "error",
                source_key=source_key,
                excerpt=str(exc),
            )
        )
        return "", problems
    registered = registry.get(source_key)
    if registered is None:
        problems.append(
            issue(
                "SOURCE_NOT_REGISTERED",
                "error",
                source_key=source_key,
                suggestion="先运行 register_recipe_sources 生成登记报告",
            )
        )
    elif registered.get("error"):
        problems.append(
            issue(
                "SOURCE_REGISTRY_ERROR",
                "error",
                source_key=source_key,
                excerpt=str(registered.get("error")),
            )
        )
    else:
        registered_hash = registered.get("sha256")
        if registered_hash != actual_hash:
            problems.append(
                issue(
                    "SOURCE_HASH_MISMATCH",
                    "error",
                    source_key=source_key,
                    excerpt=f"registered={registered_hash}; actual={actual_hash}",
                    suggestion="重新登记后再验收；报告可能已过期",
                )
            )
    if expected_hash and expected_hash != actual_hash:
        problems.append(
            issue(
                "MANIFEST_HASH_MISMATCH",
                "error",
                source_key=source_key,
                excerpt=f"manifest={expected_hash}; actual={actual_hash}",
            )
        )
    return actual_hash, problems


def should_process(ctx: AuditContext, source_key: str, source_hash: str) -> bool:
    # PDF parsing is a separate input that can change without changing the PDF bytes.
    if ctx.force or source_key.endswith(".pdf") or source_key not in ctx.cached_parents:
        return True
    prior = ctx.previous_state.get("sources", {}).get(source_key)
    if not prior:
        return True
    if prior.get("status") != "ok":
        return True
    return not (
        prior.get("sha256") == source_hash
        and prior.get("rule_version") == ctx.rule_version
        and prior.get("parser_version") == ctx.parser_version
    )


def field_text(text: str, label: str, next_labels: Iterable[str]) -> str | None:
    nxt = "|".join(re.escape(item) for item in next_labels)
    pattern = rf"{re.escape(label)}\s*[:：]\s*(.*?)(?={nxt}|$)"
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def number_field(text: str, label: str, next_labels: Iterable[str]) -> int | None:
    value = field_text(text, label, next_labels)
    if value is None:
        return None
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def parse_numbered_steps(text: str) -> tuple[list[dict[str, Any]], bool]:
    matches = NUMBERED_STEP.findall(text)
    steps: list[dict[str, Any]] = []
    numbers: list[int] = []
    for number, content in matches:
        numbers.append(int(number))
        steps.append({"ordinal": int(number), "text": content.strip(), "raw": content.strip()})
    contiguous = numbers == list(range(1, len(numbers) + 1)) and bool(numbers)
    return steps, contiguous


def ingredient_from_text(raw: str) -> dict[str, Any]:
    text = raw.strip().lstrip("-•* ").strip()
    match = QUANTITY.search(text)
    if not match:
        status = "nonstandard" if NONSTANDARD_QTY.search(text) else "unknown"
        return {
            "name": text or None,
            "quantity": None,
            "unit": None,
            "quantity_status": status,
            "raw": text,
        }
    approx = match.group("approx")
    unit = match.group("unit")
    status = "approximate" if approx else "known"
    if unit == "勺":
        status = "nonstandard"
    return {
        "name": match.group("name").strip(),
        "quantity": float(match.group("value")),
        "unit": unit,
        "quantity_status": status,
        "raw": text,
    }

def classify_status(issues: list[dict[str, Any]], *, parsed: bool, loaded: bool) -> str:
    codes = {item["code"] for item in issues}
    hard_reject = {
        "SOURCE_MISSING_FILE",
        "SOURCE_READ_FAILED",
        "PARSE_EMPTY",
        "UTF8_DECODE_ERROR",
        "INVALID_RECORD",
        "MANIFEST_HASH_MISMATCH",
        "SOURCE_HASH_MISMATCH",
        "PARSED_ARTIFACT_MISSING",
    }
    has_hard = any(
        item["severity"] == "error" and item["code"] in hard_reject for item in issues
    )
    if has_hard and not loaded:
        return "rejected"
    has_error = any(item["severity"] == "error" for item in issues)
    if loaded and ("RIGHTS_UNVERIFIED" in codes or has_error):
        return "needs_review"
    if loaded:
        return "loaded"
    if parsed:
        return "parsed"
    return "discovered"


def capabilities_for(
    *,
    name: str,
    ingredients: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    steps_ok: bool,
    servings: object,
    issues: list[dict[str, Any]],
    kind: str,
) -> dict[str, bool]:
    has_ingredients = bool(ingredients) and all(
        nonempty(item.get("name") or item.get("key")) for item in ingredients
    )
    has_steps = bool(steps) and steps_ok and all(nonempty(item.get("text")) for item in steps)
    quantities_known = has_ingredients and all(
        item.get("quantity_status") == "known" and positive_number(item.get("quantity"))
        for item in ingredients
    )
    servings_known = positive_number(servings)
    rights_blocked = any(item["code"] == "RIGHTS_UNVERIFIED" for item in issues)
    error_blocked = any(item["severity"] == "error" for item in issues)
    return {
        "discoverable": bool(name and has_ingredients),
        "chunkable": bool(name and has_ingredients and has_steps) and not error_blocked,
        "calculate_quantity": bool(
            name and has_ingredients and quantities_known and servings_known
        ),
        "allergen_hard_filter": False,
        "publishable_candidate": False
        if rights_blocked or kind in {"legacy", "external"}
        else bool(name and has_ingredients and has_steps)
        and not error_blocked
        and not rights_blocked,
    }


def build_parent(
    *,
    source_key: str,
    source_hash: str,
    record_key: str,
    name: str,
    kind: str,
    fields: dict[str, Any],
    ingredients: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    rule_version: str,
    parser_version: str,
    parsed_uri: str | None = None,
    parsed_hash: str | None = None,
    source_version: int | None = None,
    stable_document_id: str | None = None,
    steps_ok: bool = False,
) -> dict[str, Any]:
    loaded = bool(name) and bool(ingredients or steps)
    status = classify_status(issues, parsed=True, loaded=loaded)
    caps = capabilities_for(
        name=name,
        ingredients=ingredients,
        steps=steps,
        steps_ok=steps_ok,
        servings=fields.get("servings"),
        issues=issues,
        kind=kind,
    )
    return {
        "document_id": stable_document_id or document_id(source_key, record_key),
        "source_asset_key": source_key,
        "source_sha256": source_hash,
        "source_record_key": record_key,
        "source_kind": kind,
        "source_version": source_version,
        "parsed_uri": parsed_uri,
        "parsed_sha256": parsed_hash,
        "rule_version": rule_version,
        "parser_version": parser_version,
        "name": name or None,
        "fields": fields,
        "ingredients": ingredients,
        "steps": steps,
        "status": status,
        "publishable": False,
        "capabilities": caps,
        "field_confidence": {
            "name": "high" if name else "unknown",
            "ingredients": "high" if ingredients else "unknown",
            "steps": "high" if steps_ok else ("low" if steps else "unknown"),
            "quantities": (
                "high" if caps["calculate_quantity"] else ("low" if ingredients else "unknown")
            ),
            "allergens": "unknown",
            "rights": "unverified"
            if any(i["code"] == "RIGHTS_UNVERIFIED" for i in issues)
            else "declared",
        },
        "issues": issues,
    }

def load_sample(entry: dict[str, Any], ctx: AuditContext) -> dict[str, Any] | None:
    source_key = str(entry["path"]).replace("\\", "/")
    expected = entry.get("sha256")
    source_hash, problems = verify_source(source_key, ctx.registry, expected)
    record_key = entry["id"]
    if not should_process(ctx, source_key, source_hash or "missing"):
        ctx.skipped_keys.add(source_key)
        return ctx.cached_parents[source_key][0]
    ctx.processed_keys.add(source_key)
    fmt = entry.get("format")
    parsed_uri = None
    parsed_hash = None
    text = ""
    try:
        if fmt == "pdf":
            parsed_path = ctx.parsed_pdf_dir / f"{record_key}.md"
            if not parsed_path.is_file():
                problems.append(
                    issue(
                        "PARSED_ARTIFACT_MISSING",
                        "error",
                        source_key=source_key,
                        record_key=record_key,
                        excerpt=str(parsed_path),
                        suggestion="提供对应 MinerU 解析结果后再验收",
                    )
                )
                return build_parent(
                    source_key=source_key,
                    source_hash=source_hash,
                    record_key=record_key,
                    name=entry.get("name") or "",
                    kind="sample",
                    fields={"source_name": entry.get("source")},
                    ingredients=[],
                    steps=[],
                    issues=problems,
                    rule_version=ctx.rule_version,
                    parser_version=ctx.parser_version,
                    source_version=entry.get("version"),
                    stable_document_id=record_key,
                )
            text = read_utf8(parsed_path)
            parsed_hash = sha256_text(text)
            parsed_uri = parsed_path.relative_to(ROOT).as_posix()
            if not text.strip():
                problems.append(
                    issue("PARSE_EMPTY", "error", source_key=source_key, record_key=record_key)
                )
        else:
            text = read_utf8(ROOT / source_key)
            if not text.strip():
                problems.append(
                    issue("PARSE_EMPTY", "error", source_key=source_key, record_key=record_key)
                )
    except UnicodeError as exc:
        problems.append(
            issue(
                "UTF8_DECODE_ERROR",
                "error",
                source_key=source_key,
                record_key=record_key,
                excerpt=str(exc),
            )
        )
        return build_parent(
            source_key=source_key,
            source_hash=source_hash,
            record_key=record_key,
            name=entry.get("name") or "",
            kind="sample",
            fields={},
            ingredients=[],
            steps=[],
            issues=problems,
            rule_version=ctx.rule_version,
            parser_version=ctx.parser_version,
            source_version=entry.get("version"),
            stable_document_id=record_key,
        )
    except OSError as exc:
        problems.append(
            issue(
                "SOURCE_READ_FAILED",
                "error",
                source_key=source_key,
                record_key=record_key,
                excerpt=str(exc),
            )
        )
        return build_parent(
            source_key=source_key,
            source_hash=source_hash,
            record_key=record_key,
            name=entry.get("name") or "",
            kind="sample",
            fields={},
            ingredients=[],
            steps=[],
            issues=problems,
            rule_version=ctx.rule_version,
            parser_version=ctx.parser_version,
            source_version=entry.get("version"),
            stable_document_id=record_key,
        )

    title_match = re.search(r"(?m)^#\s+(.+)$", text)
    title = title_match.group(1).strip() if title_match else None
    name = field_text(text, "菜名", SAMPLE_FIELD_ORDER) or title or entry.get("name") or ""
    if entry.get("name") and name and name_key(name) != name_key(entry["name"]):
        problems.append(
            issue(
                "NAME_MISMATCH",
                "error",
                source_key=source_key,
                record_key=record_key,
                field_name="name",
                excerpt=f"清单={entry['name']}；解析={name}",
            )
        )
    id_match = re.search(r"r\s*load-\s*0*(\d+)", text, flags=re.IGNORECASE)
    if fmt == "pdf":
        parsed_id = f"rload-{int(id_match.group(1)):03d}" if id_match else None
        if parsed_id != record_key:
            problems.append(
                issue(
                    "PARSED_ID_MISMATCH",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                    excerpt="解析文本中的 ID 不完整或不匹配；使用清单 ID",
                    suggestion="保留清单权威 ID，不要静默改写源文本",
                )
            )

    ingredients_section = field_text(text, "食材（基于上述份数）", ("做法步骤", "完成判断"))
    if ingredients_section is None:
        parts = re.split(r"食材（基于上述份数）", text, maxsplit=1)
        if len(parts) == 2:
            ingredients_section = re.split(r"做法步骤", parts[1], maxsplit=1)[0]
    ingredients: list[dict[str, Any]] = []
    if ingredients_section:
        for body in re.split(r"-\s+", ingredients_section):
            item = body.strip()
            if not item:
                continue
            if item.count(" g") + item.count(" mL") + item.count(" L") > 1 and "\n" not in item:
                chunks = re.findall(
                    r"[^\n]+?(?:\d+(?:\.\d+)?\s*(?:kg|mL|g|L|个|条|根|瓣|勺)[^\n]*)",
                    item,
                )
                if len(chunks) > 1:
                    ingredients.extend(ingredient_from_text(chunk) for chunk in chunks)
                    continue
            ingredients.append(ingredient_from_text(item))
    if not ingredients:
        problems.append(
            issue(
                "INGREDIENTS_MISSING",
                "error",
                source_key=source_key,
                record_key=record_key,
                suggestion="保留未拆开的原文并人工拆条",
            )
        )
    else:
        for item in ingredients:
            if item.get("quantity_status") != "known":
                problems.append(
                    issue(
                        "INGREDIENT_QUANTITY_UNCLEAR",
                        "warning",
                        source_key=source_key,
                        record_key=record_key,
                        field_name="ingredients",
                        excerpt=item.get("raw"),
                    )
                )

    steps_block = ""
    split_steps = re.split(r"做法步骤", text, maxsplit=1)
    if len(split_steps) == 2:
        steps_block = re.split(r"完成判断|可选替换及限制|来源", split_steps[1], maxsplit=1)[0]
    steps, steps_ok = parse_numbered_steps(steps_block)
    if not steps_ok:
        problems.append(
            issue(
                "STEPS_INVALID",
                "error",
                source_key=source_key,
                record_key=record_key,
                suggestion="步骤缺失或编号不连续，不能作为完整做法切分",
            )
        )

    fields = {
        "category": field_text(text, "类别", SAMPLE_FIELD_ORDER),
        "servings": number_field(text, "基准份数", SAMPLE_FIELD_ORDER),
        "prep_minutes": number_field(text, "准备时间", SAMPLE_FIELD_ORDER),
        "cook_minutes": number_field(text, "烹饪时间", SAMPLE_FIELD_ORDER),
        "total_minutes": number_field(text, "预计总时间", SAMPLE_FIELD_ORDER),
        "equipment": field_text(text, "设备", SAMPLE_FIELD_ORDER),
        # This metadata occupies one line; otherwise the next ingredient heading can be
        # swallowed into the note when the PDF extractor omits Markdown boundaries.
        "allergen_note": (
            (field_text(text, "过敏原与核对", SAMPLE_FIELD_ORDER) or "").splitlines()[0].strip()
            or None
        ),
        "completion_criteria": field_text(text, "完成判断", SAMPLE_FIELD_ORDER),
        "substitution_note": field_text(text, "可选替换及限制", SAMPLE_FIELD_ORDER),
        "source_name": field_text(text, "来源", SAMPLE_FIELD_ORDER) or entry.get("source"),
        "allergens": None,
    }
    for required in ("servings", "allergen_note"):
        if not fields.get(required):
            problems.append(
                issue(
                    "FIELD_MISSING",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                    field_name=required,
                    excerpt=f"缺少 {required}",
                )
            )
    problems.append(
        issue(
            "ALLERGENS_UNVERIFIED",
            "warning",
            source_key=source_key,
            record_key=record_key,
            field_name="allergens",
            suggestion="自由文本过敏原不得当作无过敏原或启用严格过滤",
        )
    )
    return build_parent(
        source_key=source_key,
        source_hash=source_hash,
        record_key=record_key,
        name=name,
        kind="sample",
        fields=fields,
        ingredients=ingredients,
        steps=steps,
        issues=problems,
        rule_version=ctx.rule_version,
        parser_version=ctx.parser_version,
        parsed_uri=parsed_uri,
        parsed_hash=parsed_hash,
        source_version=entry.get("version"),
        stable_document_id=record_key,
        steps_ok=steps_ok,
    )

def assess_json_row(
    *,
    source_key: str,
    source_hash: str,
    kind: str,
    index: int,
    recipe: object,
    name_counts: Counter[str],
    bv_counts: Counter[str],
    rule_version: str,
    parser_version: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    problems: list[dict[str, Any]] = []
    identity_notes: list[dict[str, Any]] = []
    if not isinstance(recipe, dict):
        problems.append(
            issue("INVALID_RECORD", "error", source_key=source_key, record_key=f"ordinal:{index}")
        )
        recipe = {}
    name = recipe.get("name") if nonempty(recipe.get("name")) else ""
    bv = (recipe.get("bv") or "").strip() if isinstance(recipe.get("bv"), str) else ""

    if kind == "seed":
        if name and name_counts[name] == 1:
            record_key = f"name:{name}"
        else:
            record_key = f"ordinal:{index}"
            identity_notes.append(
                issue(
                    "IDENTITY_UNSTABLE",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                    suggestion="种子记录缺少显式稳定 ID；当前用唯一菜名映射，冲突则降级待审",
                )
            )
    elif bv and bv_counts[bv] == 1:
        record_key = f"bv:{bv}"
    elif name and name_counts[name] == 1:
        record_key = f"name:{name}"
        identity_notes.append(
            issue(
                "IDENTITY_FROM_NAME",
                "warning",
                source_key=source_key,
                record_key=record_key,
                suggestion="未使用单独 bv 作为长期主键；已用唯一菜名线索",
            )
        )
    else:
        record_key = f"ordinal:{index}"
        identity_notes.append(
            issue(
                "IDENTITY_UNSTABLE",
                "error",
                source_key=source_key,
                record_key=record_key,
                suggestion="bv 空/重复且菜名不可唯一映射；保持待审核，不能静默发布",
            )
        )

    problems.extend(identity_notes)
    ingredients: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    steps_ok = False

    raw_steps = recipe.get("steps")
    if not isinstance(raw_steps, list):
        problems.append(
            issue("STEPS_INVALID", "error", source_key=source_key, record_key=record_key)
        )
    elif not raw_steps:
        problems.append(
            issue("STEPS_MISSING", "error", source_key=source_key, record_key=record_key)
        )
    elif any(not nonempty(step) for step in raw_steps):
        problems.append(
            issue("STEPS_INVALID", "error", source_key=source_key, record_key=record_key)
        )
    else:
        steps_ok = True
        steps = [
            {"ordinal": i + 1, "text": str(step).strip(), "raw": str(step).strip()}
            for i, step in enumerate(raw_steps)
        ]

    if kind == "seed":
        raw_ingredients = recipe.get("ingredients")
        has_ingredients = isinstance(raw_ingredients, list) and bool(raw_ingredients)
        quantities_known = has_ingredients
        if has_ingredients:
            for item in raw_ingredients:
                if not isinstance(item, dict) or not nonempty(item.get("key")):
                    has_ingredients = quantities_known = False
                    problems.append(
                        issue(
                            "INGREDIENT_INVALID",
                            "error",
                            source_key=source_key,
                            record_key=record_key,
                        )
                    )
                    break
                qty_ok = positive_number(item.get("quantity")) and nonempty(item.get("unit"))
                if not qty_ok:
                    quantities_known = False
                ingredients.append(
                    {
                        "name": item.get("key"),
                        "key": item.get("key"),
                        "quantity": item.get("quantity"),
                        "unit": item.get("unit"),
                        "optional": item.get("optional"),
                        "quantity_status": "known" if qty_ok else "unknown",
                        "raw": json.dumps(item, ensure_ascii=False),
                    }
                )
        if not has_ingredients and not any(i["code"] == "INGREDIENT_INVALID" for i in problems):
            problems.append(
                issue("INGREDIENTS_MISSING", "error", source_key=source_key, record_key=record_key)
            )
        if has_ingredients and not quantities_known:
            problems.append(
                issue(
                    "QUANTITY_INVALID_OR_MISSING",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                )
            )
        if not positive_number(recipe.get("servings")):
            problems.append(
                issue(
                    "SERVINGS_INVALID_OR_MISSING",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                    field_name="servings",
                )
            )
        if not positive_number(recipe.get("duration_minutes")):
            problems.append(
                issue(
                    "DURATION_INVALID_OR_MISSING",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                    field_name="duration_minutes",
                )
            )
        for nutrition_field in ("kcal_per_serving", "estimated_cost_cents"):
            value = recipe.get(nutrition_field)
            if value is not None and not nonnegative_number(value):
                problems.append(
                    issue(
                        "NUTRITION_OR_COST_INVALID",
                        "warning",
                        source_key=source_key,
                        record_key=record_key,
                        field_name=nutrition_field,
                    )
                )
        allergens = recipe.get("allergens")
        if not isinstance(allergens, list) or any(not nonempty(value) for value in allergens):
            problems.append(
                issue("ALLERGENS_INVALID", "warning", source_key=source_key, record_key=record_key)
            )
        if not nonempty(recipe.get("source_name")) or not nonempty(recipe.get("license_note")):
            problems.append(
                issue(
                    "SOURCE_OR_LICENSE_MISSING",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                )
            )
        if nonempty(recipe.get("source_url")) and not web_url(recipe["source_url"]):
            problems.append(
                issue("SOURCE_URL_INVALID", "warning", source_key=source_key, record_key=record_key)
            )
        fields = {
            "servings": recipe.get("servings"),
            "duration_minutes": recipe.get("duration_minutes"),
            "allergens": recipe.get("allergens"),
            "tags": recipe.get("tags"),
            "source_name": recipe.get("source_name"),
            "license_note": recipe.get("license_note"),
            "source_url": recipe.get("source_url") or None,
        }
    else:
        has_ingredients = nonempty(recipe.get("stuff"))
        if not has_ingredients:
            problems.append(
                issue("INGREDIENTS_MISSING", "error", source_key=source_key, record_key=record_key)
            )
        else:
            for part in re.split(r"[、,，]", str(recipe.get("stuff"))):
                part = part.strip()
                if part:
                    ingredients.append(
                        {
                            "name": part,
                            "quantity": None,
                            "unit": None,
                            "quantity_status": "unknown",
                            "raw": part,
                        }
                    )
            problems.append(
                issue(
                    "QUANTITY_UNKNOWN",
                    "warning",
                    source_key=source_key,
                    record_key=record_key,
                    excerpt="原始 stuff 未提供可信用量",
                )
            )
        if not nonempty(recipe.get("source")) and not nonempty(recipe.get("bv")):
            problems.append(
                issue("SOURCE_MISSING", "warning", source_key=source_key, record_key=record_key)
            )
        if nonempty(recipe.get("source")) and not web_url(recipe["source"]):
            problems.append(
                issue("SOURCE_URL_INVALID", "warning", source_key=source_key, record_key=record_key)
            )
        problems.append(
            issue(
                "RIGHTS_UNVERIFIED",
                "warning",
                source_key=source_key,
                record_key=record_key,
                suggestion="来源及使用权未核实，不能进入生产检索或 App 展示",
            )
        )
        fields = {
            "servings": None,
            "duration_minutes": None,
            "allergens": None,
            "tags": recipe.get("tags"),
            "source_name": recipe.get("source"),
            "external_bv": bv or None,
            "license_note": None,
        }

    if not name:
        problems.append(
            issue("NAME_MISSING", "error", source_key=source_key, record_key=record_key)
        )

    parent = build_parent(
        source_key=source_key,
        source_hash=source_hash,
        record_key=record_key,
        name=str(name or ""),
        kind=kind,
        fields=fields,
        ingredients=ingredients,
        steps=steps,
        issues=problems,
        rule_version=rule_version,
        parser_version=parser_version,
        steps_ok=steps_ok,
    )
    return parent, identity_notes


def load_json_file(source_key: str, kind: str, ctx: AuditContext) -> list[dict[str, Any]]:
    source_hash, problems = verify_source(source_key, ctx.registry)
    if not should_process(ctx, source_key, source_hash or "missing"):
        ctx.skipped_keys.add(source_key)
        cached = ctx.cached_parents[source_key]
        ctx.issues.extend(item for parent in cached for item in parent["issues"])
        return cached
    ctx.processed_keys.add(source_key)
    if any(p["severity"] == "error" for p in problems):
        ctx.issues.extend(problems)
        ctx.file_results.append(
            {"source_key": source_key, "kind": kind, "result": "error", "issues": problems}
        )
        return []
    try:
        payload = json.loads(read_utf8(ROOT / source_key))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        err = issue("SOURCE_READ_FAILED", "error", source_key=source_key, excerpt=str(exc))
        ctx.issues.append(err)
        ctx.file_results.append(
            {"source_key": source_key, "kind": kind, "result": "error", "issues": [err]}
        )
        return []
    if kind == "seed":
        if not isinstance(payload, dict) or not isinstance(payload.get("recipes"), list):
            raise ValueError(f"菜谱 JSON 顶层不是含 recipes 的对象：{source_key}")
        rows = payload["recipes"]
    else:
        if not isinstance(payload, list):
            raise ValueError(f"菜谱 JSON 顶层不是列表：{source_key}")
        rows = payload

    name_counts: Counter[str] = Counter()
    bv_counts: Counter[str] = Counter()
    for row in rows:
        if isinstance(row, dict):
            if nonempty(row.get("name")):
                name_counts[str(row["name"]).strip()] += 1
            bv = row.get("bv")
            if isinstance(bv, str) and bv.strip():
                bv_counts[bv.strip()] += 1

    parents: list[dict[str, Any]] = []
    clue_map: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        parent, identity_notes = assess_json_row(
            source_key=source_key,
            source_hash=source_hash,
            kind=kind,
            index=index,
            recipe=row,
            name_counts=name_counts,
            bv_counts=bv_counts,
            rule_version=ctx.rule_version,
            parser_version=ctx.parser_version,
        )
        clue_map.setdefault(parent["source_record_key"], []).append(index)
        parents.append(parent)
        ctx.issues.extend(parent["issues"])
        for note in identity_notes:
            ctx.identity_conflicts.append(
                {
                    "source_key": source_key,
                    "source_record_key": parent["source_record_key"],
                    "document_id": parent["document_id"],
                    "code": note["code"],
                    "ordinal": index,
                }
            )

    for clue, ordinals in clue_map.items():
        if len(ordinals) > 1:
            ctx.identity_conflicts.append(
                {
                    "source_key": source_key,
                    "source_record_key": clue,
                    "code": "IDENTITY_CONFLICT",
                    "ordinals": ordinals,
                }
            )
            for parent in parents:
                if parent["source_record_key"] == clue:
                    conflict = issue(
                        "IDENTITY_CONFLICT",
                        "error",
                        source_key=source_key,
                        record_key=clue,
                        excerpt=f"clue maps to ordinals {ordinals}",
                    )
                    parent["issues"].append(conflict)
                    parent["status"] = "needs_review"
                    parent["publishable"] = False
                    ctx.issues.append(conflict)

    names: dict[str, list[dict[str, Any]]] = {}
    for parent in parents:
        if parent.get("name"):
            names.setdefault(name_key(parent["name"]), []).append(parent)
    for group in names.values():
        if len(group) > 1:
            for parent in group:
                dup = issue(
                    "POSSIBLE_DUPLICATE_NAME",
                    "warning",
                    source_key=source_key,
                    record_key=parent["source_record_key"],
                    field_name="name",
                    excerpt=parent["name"],
                    suggestion="仅提示人工复核，不按菜名自动合并",
                )
                parent["issues"].append(dup)
                ctx.issues.append(dup)

    ctx.file_results.append(
        {"source_key": source_key, "kind": kind, "result": "ok", "records": len(parents)}
    )
    return parents

def load_external(manifest_path: Path, ctx: AuditContext) -> dict[str, Any] | None:
    manifest = json.loads(read_utf8(manifest_path))
    base = manifest_path.parent
    files = {item["role"]: item for item in manifest.get("files", []) if "role" in item}
    original = files.get("original")
    parsed = files.get("parsed_markdown")
    if not original or not parsed:
        raise ValueError(f"外部清单缺少 original/parsed_markdown：{manifest_path}")
    source_path = (base / original["path"]).resolve()
    source_key = source_path.relative_to(ROOT).as_posix()
    source_hash, problems = verify_source(source_key, ctx.registry, original.get("sha256"))
    if not should_process(ctx, source_key, source_hash or "missing"):
        ctx.skipped_keys.add(source_key)
        return None
    ctx.processed_keys.add(source_key)
    parsed_path = (base / parsed["path"]).resolve()
    try:
        text = read_utf8(parsed_path)
        parsed_hash, _ = fingerprint(parsed_path)
    except (OSError, UnicodeError, ValueError) as exc:
        problems.append(
            issue(
                "PARSED_ARTIFACT_MISSING",
                "error",
                source_key=source_key,
                excerpt=str(exc),
            )
        )
        return build_parent(
            source_key=source_key,
            source_hash=source_hash,
            record_key=manifest.get("source_id") or source_key,
            name=manifest.get("title") or "",
            kind="external",
            fields={"rights_status": manifest.get("rights_status")},
            ingredients=[],
            steps=[],
            issues=problems
            + [
                issue(
                    "RIGHTS_UNVERIFIED",
                    "warning",
                    source_key=source_key,
                    suggestion="外部来源使用权未核实",
                )
            ],
            rule_version=ctx.rule_version,
            parser_version=ctx.parser_version,
            stable_document_id=document_id(source_key, manifest.get("source_id") or "external"),
        )
    expected_parsed = parsed.get("sha256")
    if expected_parsed and expected_parsed != parsed_hash:
        problems.append(
            issue(
                "PARSED_HASH_MISMATCH",
                "error",
                source_key=source_key,
                excerpt=f"manifest={expected_parsed}; actual={parsed_hash}",
            )
        )

    ingredients: list[dict[str, Any]] = []
    section_match = re.search(r"###\s*用料\s*\n(.*?)(?=\n###|\Z)", text, flags=re.DOTALL)
    if section_match:
        lines = [line.strip() for line in section_match.group(1).splitlines() if line.strip()]
        index = 0
        while index < len(lines):
            name = lines[index]
            qty = lines[index + 1] if index + 1 < len(lines) else ""
            if re.fullmatch(r"步骤\s+\d+", name) or name.startswith("#"):
                break
            if index + 1 < len(lines) and (
                NONSTANDARD_QTY.search(qty) or re.search(r"\d", qty) or qty in {"适量", "少许"}
            ):
                raw = f"{name} {qty}".strip()
                item = ingredient_from_text(raw)
                if qty.strip() in {"适量", "少许"} or "/" in qty or "勺" in qty:
                    item["quantity_status"] = "nonstandard"
                    if qty.strip() in {"适量", "少许"} or "/" in qty:
                        item["quantity"] = None
                        item["unit"] = None
                item["name"] = name
                item["raw"] = raw
                ingredients.append(item)
                index += 2
            else:
                ingredients.append(ingredient_from_text(name))
                index += 1
                problems.append(
                    issue(
                        "INGREDIENT_PAIR_INVALID",
                        "warning",
                        source_key=source_key,
                        excerpt=name,
                        suggestion="用料名称与数量未能成对解析",
                    )
                )
    if not ingredients:
        problems.append(issue("INGREDIENTS_MISSING", "error", source_key=source_key))

    step_matches = list(re.finditer(r"(?m)^步骤\s+(\d+)\s*$", text))
    steps: list[dict[str, Any]] = []
    for i, match in enumerate(step_matches):
        start = match.end()
        end = step_matches[i + 1].start() if i + 1 < len(step_matches) else len(text)
        block = text[start:end].strip()
        ordinal = int(match.group(1))
        lines = [line for line in block.splitlines() if line.strip()]
        for line in lines:
            if "![" in line and "纯奶手撕吐司" in line:
                problems.append(
                    issue(
                        "IMAGE_ALT_MISMATCH",
                        "error",
                        source_key=source_key,
                        record_key=manifest.get("source_id"),
                        field_name="steps",
                        excerpt=line[:120],
                        suggestion="步骤图片说明混入其他菜名，需对照原 PDF 核验",
                    )
                )
        prose = "\n".join(line for line in lines if not line.strip().startswith("!["))
        steps.append({"ordinal": ordinal, "text": prose.strip(), "raw": block})
    numbers = [step["ordinal"] for step in steps]
    steps_ok = numbers == list(range(1, len(numbers) + 1)) and bool(numbers)
    if not steps_ok:
        problems.append(
            issue(
                "STEPS_INVALID",
                "error",
                source_key=source_key,
                suggestion="外部文档步骤缺失或编号异常",
            )
        )

    problems.append(
        issue(
            "RIGHTS_UNVERIFIED",
            "warning",
            source_key=source_key,
            suggestion="来源及使用权待核实，不能进入可发布/可检索集合",
        )
    )
    problems.append(
        issue(
            "PORTION_UNKNOWN",
            "warning",
            source_key=source_key,
            field_name="servings",
            excerpt="原文未提供基准份数",
        )
    )
    problems.append(
        issue(
            "ALLERGENS_UNVERIFIED",
            "warning",
            source_key=source_key,
            field_name="allergens",
        )
    )

    record_key = manifest.get("source_id") or source_key
    fields = {
        "servings": None,
        "duration_minutes": None,
        "allergens": None,
        "source_name": manifest.get("attribution_in_document"),
        "rights_status": manifest.get("rights_status") or "unverified",
    }
    parent = build_parent(
        source_key=source_key,
        source_hash=source_hash,
        record_key=record_key,
        name=manifest.get("title") or "",
        kind="external",
        fields=fields,
        ingredients=ingredients,
        steps=steps,
        issues=problems,
        rule_version=ctx.rule_version,
        parser_version=ctx.parser_version,
        parsed_uri=parsed_path.relative_to(ROOT).as_posix(),
        parsed_hash=parsed_hash,
        stable_document_id=document_id(source_key, record_key),
        steps_ok=steps_ok,
    )
    parent["status"] = "needs_review"
    parent["publishable"] = False
    parent["capabilities"]["publishable_candidate"] = False
    return parent

def select_manual_review(
    parents: list[dict[str, Any]], size: int, seed: int
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def take(group: list[dict[str, Any]], limit: int) -> None:
        candidates = list(group)
        rng.shuffle(candidates)
        for item in candidates[:limit]:
            if item["document_id"] in seen:
                continue
            selected.append(item)
            seen.add(item["document_id"])

    take([p for p in parents if p["source_kind"] == "sample"], 3)
    take([p for p in parents if p["source_kind"] == "seed"], 3)
    take([p for p in parents if any(i["code"] == "STEPS_MISSING" for i in p["issues"])], 3)
    take([p for p in parents if any(i["code"] == "STEPS_INVALID" for i in p["issues"])], 3)
    take(
        [
            p
            for p in parents
            if p["capabilities"].get("chunkable") and p["source_kind"] == "legacy"
        ],
        3,
    )
    take(
        [p for p in parents if any(i["code"] == "POSSIBLE_DUPLICATE_NAME" for i in p["issues"])],
        3,
    )
    take([p for p in parents if any(i["code"] == "PARSED_ID_MISMATCH" for i in p["issues"])], 3)
    take([p for p in parents if any(i["code"] == "IMAGE_ALT_MISMATCH" for i in p["issues"])], 3)

    # All errors and unverified-rights sources enter the todo list.
    for parent in parents:
        codes = {i["code"] for i in parent["issues"]}
        severities = {i["severity"] for i in parent["issues"]}
        if "error" in severities or "RIGHTS_UNVERIFIED" in codes:
            if parent["document_id"] not in seen:
                selected.append(parent)
                seen.add(parent["document_id"])

    remaining = [p for p in parents if p["document_id"] not in seen]
    rng.shuffle(remaining)
    selected.extend(remaining[: max(0, size - len(selected))])
    return selected


def exclusion_reasons(parent: dict[str, Any]) -> list[str]:
    """Choose a conservative, traceable corpus for the next chunking stage."""
    reasons: list[str] = []
    if parent["status"] != "loaded":
        reasons.append("NOT_LOADED")
    if not parent["capabilities"].get("chunkable"):
        reasons.append("INCOMPLETE_RECIPE")
    if not parent["capabilities"].get("publishable_candidate"):
        reasons.append("SOURCE_NOT_CLEARED")
    codes = {item["code"] for item in parent["issues"]}
    if "INGREDIENT_QUANTITY_UNCLEAR" in codes:
        reasons.append("INGREDIENT_PARSE_REVIEW")
    return reasons


def write_reports_atomic(
    output_dir: Path,
    *,
    summary: dict[str, Any],
    parents: Iterable[dict[str, Any]],
    issues: Iterable[dict[str, Any]],
    reviews: list[dict[str, Any]],
    ready: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".tmp-audit-") as tmp:
        tmp_path = Path(tmp)
        parents_path = tmp_path / "parents.jsonl"
        issues_path = tmp_path / "issues.jsonl"
        summary_path = tmp_path / "summary.json"
        review_path = tmp_path / "manual_review.csv"
        state_path = tmp_path / ".audit_state.json"
        ready_path = tmp_path / "ready_for_chunking.jsonl"
        excluded_path = tmp_path / "excluded.jsonl"

        with parents_path.open("w", encoding="utf-8", newline="\n") as handle:
            for parent in parents:
                handle.write(json.dumps(parent, ensure_ascii=False) + "\n")
        with issues_path.open("w", encoding="utf-8", newline="\n") as handle:
            for item in issues:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        with ready_path.open("w", encoding="utf-8", newline="\n") as handle:
            for parent in ready:
                handle.write(json.dumps(parent, ensure_ascii=False) + "\n")
        with excluded_path.open("w", encoding="utf-8", newline="\n") as handle:
            for item in excluded:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "document_id",
                    "source_key",
                    "source_kind",
                    "source_record_key",
                    "name",
                    "status",
                    "publishable",
                    "issue_codes",
                    "chunkable",
                    "rights_review",
                    "source_matches",
                    "ingredients_match",
                    "steps_match",
                    "allergen_review",
                    "reviewer",
                    "review_date",
                    "notes",
                ),
            )
            writer.writeheader()
            for item in reviews:
                writer.writerow(
                    {
                        "document_id": item["document_id"],
                        "source_key": item["source_asset_key"],
                        "source_kind": item["source_kind"],
                        "source_record_key": item["source_record_key"],
                        "name": item.get("name") or "",
                        "status": item.get("status"),
                        "publishable": item.get("publishable"),
                        "issue_codes": ";".join(i["code"] for i in item.get("issues", [])),
                        "chunkable": item.get("capabilities", {}).get("chunkable"),
                        "rights_review": "",
                        "source_matches": "",
                        "ingredients_match": "",
                        "steps_match": "",
                        "allergen_review": "",
                        "reviewer": "",
                        "review_date": "",
                        "notes": "",
                    }
                )
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for name in (
            "parents.jsonl",
            "issues.jsonl",
            "summary.json",
            "manual_review.csv",
            "ready_for_chunking.jsonl",
            "excluded.jsonl",
            ".audit_state.json",
        ):
            os.replace(tmp_path / name, output_dir / name)


def run_audit(
    *,
    registry_path: Path | None,
    inputs: list[Path] | None,
    manifest_path: Path,
    parsed_pdf_dir: Path,
    pending_root: Path,
    output_dir: Path,
    rule_version: str,
    parser_version: str,
    seed: int,
    sample_size: int,
    dry_run: bool,
    force: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    registry = load_registry(registry_path, inputs)
    previous_state: dict[str, Any] = {}
    state_file = output_dir / ".audit_state.json"
    if state_file.is_file():
        previous_state = json.loads(state_file.read_text(encoding="utf-8"))

    manifest_file = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    manifest_hash, _ = fingerprint(manifest_file)
    cached_parents: dict[str, list[dict[str, Any]]] = {}
    parent_file = output_dir / "parents.jsonl"
    if parent_file.is_file() and not force:
        with parent_file.open(encoding="utf-8") as handle:
            for line in handle:
                parent = json.loads(line)
                cached_parents.setdefault(parent["source_asset_key"], []).append(parent)

    parsed_dir = parsed_pdf_dir if parsed_pdf_dir.is_absolute() else ROOT / parsed_pdf_dir
    ctx = AuditContext(
        registry=registry,
        rule_version=rule_version,
        parser_version=parser_version,
        parsed_pdf_dir=parsed_dir,
        force=force or previous_state.get("manifest_sha256") != manifest_hash,
        previous_state=previous_state,
        cached_parents=cached_parents,
    )

    parents: list[dict[str, Any]] = []
    for source_key, kind in ((SOURCE_JSON, "seed"), (LEGACY_JSON, "legacy")):
        if source_key in registry or (ROOT / source_key).is_file():
            parents.extend(load_json_file(source_key, kind, ctx))

    manifest = json.loads(read_utf8(manifest_file))
    sample_parents: list[dict[str, Any]] = []
    for entry in manifest.get("recipes", []):
        parent = load_sample(entry, ctx)
        if parent is not None:
            sample_parents.append(parent)
            parents.append(parent)
            ctx.issues.extend(parent["issues"])

    pending = pending_root if pending_root.is_absolute() else ROOT / pending_root
    if pending.exists():
        for external_manifest in sorted(pending.glob("*/manifest.json")):
            parent = load_external(external_manifest, ctx)
            if parent is not None:
                parents.append(parent)
                ctx.issues.extend(parent["issues"])

    status_counts = Counter(parent["status"] for parent in parents)
    kind_counts = Counter(parent["source_kind"] for parent in parents)
    issue_counts = Counter(item["code"] for item in ctx.issues)
    severity_counts = Counter(item["severity"] for item in ctx.issues)
    capability_counts = Counter(
        key
        for parent in parents
        for key, enabled in parent.get("capabilities", {}).items()
        if enabled
    )
    sample_ids = [p["document_id"] for p in parents if p["source_kind"] == "sample"]
    expected_sample_ids = [entry["id"] for entry in manifest.get("recipes", [])]
    sample_ok = len(sample_ids) == len(expected_sample_ids) and sorted(sample_ids) == sorted(
        expected_sample_ids
    )
    reviews = select_manual_review(parents, sample_size, seed)
    ready: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for parent in parents:
        reasons = exclusion_reasons(parent)
        if reasons:
            excluded.append(
                {
                    "document_id": parent["document_id"],
                    "source_asset_key": parent["source_asset_key"],
                    "source_record_key": parent["source_record_key"],
                    "name": parent["name"],
                    "reasons": reasons,
                }
            )
        else:
            ready.append(parent)
    exclusion_counts = Counter(reason for item in excluded for reason in item["reasons"])
    elapsed = time.perf_counter() - started

    if registry_path and registry_path.is_relative_to(ROOT):
        registry_label = registry_path.relative_to(ROOT).as_posix()
    elif registry_path:
        registry_label = str(registry_path)
    else:
        registry_label = "scanned"

    summary = {
        "scope": (
            "Pre-chunk parent loading and quality audit only; "
            "no chunking, embedding, pgvector, or business publish"
        ),
        "rule_version": rule_version,
        "parser_version": parser_version,
        "registry_report": registry_label,
        "started_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "source_files_in_registry": len(registry),
        "source_files_processed": len(ctx.processed_keys),
        "source_files_skipped_unchanged": len(ctx.skipped_keys),
        "parent_records": len(parents),
        "ready_for_chunking_count": len(ready),
        "excluded_count": len(excluded),
        "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        "by_source_kind": dict(sorted(kind_counts.items())),
        "by_status": dict(sorted(status_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "capability_counts": dict(sorted(capability_counts.items())),
        "publishable_count": sum(1 for parent in parents if parent.get("publishable")),
        "chunkable_count": sum(
            1 for parent in parents if parent.get("capabilities", {}).get("chunkable")
        ),
        "identity_conflicts": len(ctx.identity_conflicts),
        "manual_review_rows": len(reviews),
        "manual_review_seed": seed,
        "sample_manifest_parent_count_ok": sample_ok,
        "sample_parent_count": len(sample_ids),
        "publication_performed": False,
        "vectorization_performed": False,
        "database_write_performed": False,
        "dry_run": dry_run,
    }

    new_state = {
        "rule_version": rule_version,
        "parser_version": parser_version,
        "manifest_sha256": manifest_hash,
        "sources": {
            key: {
                "sha256": (registry.get(key) or {}).get("sha256"),
                "rule_version": rule_version,
                "parser_version": parser_version,
                "status": (
                    "ok"
                    if key in ctx.processed_keys
                    and not any(
                        i["source_key"] == key and i["severity"] == "error" for i in ctx.issues
                    )
                    else ("ok" if key in ctx.skipped_keys else "failed")
                ),
            }
            for key in sorted(set(ctx.processed_keys) | set(ctx.skipped_keys) | set(registry))
        },
        "identity_conflicts": ctx.identity_conflicts,
    }

    summary["output_dir"] = (
        output_dir.relative_to(ROOT).as_posix()
        if output_dir.is_relative_to(ROOT)
        else str(output_dir)
    )
    if not dry_run:
        write_reports_atomic(
            output_dir,
            summary=summary,
            parents=parents,
            issues=ctx.issues,
            reviews=reviews,
            ready=ready,
            excluded=excluded,
            state=new_state,
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry-report",
        type=Path,
        help="只读来源登记 JSON 报告；省略则现场扫描输入",
    )
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        dest="inputs",
        help="显式输入文件或目录（可重复）；省略时用登记脚本默认来源",
    )
    parser.add_argument("--manifest", type=Path, default=SAMPLE_MANIFEST, help="多格式样本清单")
    parser.add_argument(
        "--parsed-pdf-dir",
        type=Path,
        default=DEFAULT_PARSED_PDF_DIR,
        help="PDF 的 MinerU 解析结果目录",
    )
    parser.add_argument(
        "--pending-root",
        type=Path,
        default=PENDING_ROOT,
        help="外部待审核来源根目录",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="报告输出目录")
    parser.add_argument("--rule-version", default=RULE_VERSION)
    parser.add_argument("--parser-version", default=PARSER_VERSION)
    parser.add_argument("--seed", type=int, default=20261107, help="人工抽检可重复种子")
    parser.add_argument("--sample-size", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true", help="只计算汇总，不写报告文件")
    parser.add_argument("--force", action="store_true", help="忽略增量状态，强制重处理全部来源")
    parser.add_argument(
        "--commit-db",
        action="store_true",
        help="显式数据库写入开关（第一版未实现，提供时直接失败）",
    )
    args = parser.parse_args(argv)
    if args.sample_size < 0:
        parser.error("--sample-size must be nonnegative")
    if args.commit_db:
        parser.error("第一版仅支持只读 JSON 报告模式；数据库写入开关尚未实现")

    def under_root(path: Path) -> Path:
        if path.is_absolute():
            return path.resolve()
        return (ROOT / path).resolve()

    output_dir = under_root(args.output_dir)
    try:
        summary = run_audit(
            registry_path=under_root(args.registry_report) if args.registry_report else None,
            inputs=[under_root(path) for path in args.inputs] if args.inputs else None,
            manifest_path=under_root(args.manifest),
            parsed_pdf_dir=under_root(args.parsed_pdf_dir),
            pending_root=under_root(args.pending_root),
            output_dir=output_dir,
            rule_version=args.rule_version,
            parser_version=args.parser_version,
            seed=args.seed,
            sample_size=args.sample_size,
            dry_run=args.dry_run,
            force=args.force,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"prechunk audit failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get("severity_counts", {}).get("error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
