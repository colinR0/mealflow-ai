"""Load the unverified HowToCook JSON into research parent records.

Dedup uses the existing lazy-cookbook cleaners. Quantities, servings, duration,
calories, and allergens are left unknown: the source does not state them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path
from typing import Any

from app.cli.register_recipe_sources import ROOT, fingerprint

RULE_VERSION = "howtocook-research-load-v1"
SOURCE_SHA256 = "7156098d5daf49fde4c91115c376cf9ef3d4bb1b3464d1c4b25b5494271a8026"
SOURCE_BYTES = 483701
DOCUMENT_NAMESPACE = uuid.UUID("6c5d4e3f-2a1b-4c5d-8e7f-1029384756ab")

_cleaners: tuple[Any, Any] | None = None


def cleaners() -> tuple[Any, Any]:
    """Load _clean_stuff and _steps without running the cookbook importer."""
    global _cleaners
    if _cleaners is None:
        path = ROOT / "scripts" / "import_lazy_cookbook.py"
        spec = importlib.util.spec_from_file_location("mealflow_import_lazy_cookbook", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载清洗函数：{path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _cleaners = (module._clean_stuff, module._steps)
    return _cleaners


def assert_source_identity(path: Path) -> tuple[str, int]:
    digest, size = fingerprint(path)
    if digest != SOURCE_SHA256 or size != SOURCE_BYTES:
        raise ValueError(f"清洗文件摘要不符：{digest} {size}")
    return digest, size


def raw_ingredient_parts(stuff: str) -> list[str]:
    parts = []
    for part in str(stuff).replace("，", "、").replace(",", "、").split("、"):
        text = part.strip()
        if text:
            parts.append(text)
    if not parts:
        raise ValueError("食材原文为空")
    return parts


def _raw_sha(entry: dict[str, Any]) -> str:
    payload = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity(entry: dict[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    clean_stuff, clean_steps = cleaners()
    name = str(entry.get("name") or "").strip()
    ingredients = tuple(clean_stuff(entry.get("stuff") or ""))
    steps = tuple(clean_steps(entry))
    return name, ingredients, steps


def _require_record(entry: object, index: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"第 {index} 条不是对象")
    name, ingredients, steps = _identity(entry)
    if not name:
        raise ValueError(f"第 {index} 条缺少菜名")
    if not isinstance(entry.get("stuff"), str) or not ingredients:
        raise ValueError(f"第 {index} 条没有可用食材：{name}")
    if not isinstance(entry.get("steps"), list) or not steps:
        raise ValueError(f"第 {index} 条没有步骤：{name}")
    return entry


def document_id_for(source_sha: str, identity: tuple[str, tuple[str, ...], tuple[str, ...]]) -> str:
    payload = json.dumps(
        {
            "source_sha256": source_sha,
            "name": identity[0],
            "ingredients": identity[1],
            "steps": identity[2],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(DOCUMENT_NAMESPACE, payload))


def _present(entry: dict[str, Any], key: str) -> Any:
    value = entry.get(key)
    if value is None or value == "":
        return None
    return value


def build_parent(
    entry: dict[str, Any], *, source_key: str, source_sha: str, index: int
) -> dict[str, Any]:
    """Keep the source text. Do not invent servings, minutes, calories, or allergens."""
    identity = _identity(entry)
    name, _ingredients, steps = identity
    return {
        "document_id": document_id_for(source_sha, identity),
        "source_asset_key": source_key,
        "source_sha256": source_sha,
        "source_record_key": f"howtocook:{index}:{name}",
        "source_kind": "howtocook-mock-clean",
        "source_version": 1,
        "parsed_uri": None,
        "parsed_sha256": None,
        "rule_version": RULE_VERSION,
        "parser_version": "howtocook-json-v1",
        "name": name,
        "fields": {
            "servings": None,
            "duration_minutes": None,
            "total_minutes": None,
            "calories_per_serving": None,
            "allergens": None,
            "category": _present(entry, "category"),
            "difficulty": _present(entry, "difficulty"),
            "tools": _present(entry, "tools"),
            "heat": _present(entry, "heat"),
            "stuff": entry.get("stuff"),
            "authorization": "unverified",
        },
        "ingredients": [
            {
                "name": part,
                "quantity": None,
                "unit": None,
                "optional": False,
                "quantity_status": "unknown",
                "raw": part,
            }
            for part in raw_ingredient_parts(str(entry.get("stuff") or ""))
        ],
        "steps": [
            {"ordinal": ordinal, "text": text, "raw": text} for ordinal, text in enumerate(steps, 1)
        ],
        "status": "loaded",
        "publishable": False,
        "capabilities": {
            "discoverable": False,
            "chunkable": True,
            "calculate_quantity": False,
            "allergen_hard_filter": False,
            "publishable_candidate": False,
            "research_candidate": True,
        },
        "field_confidence": {
            "name": "high",
            "ingredients": "high",
            "steps": "high",
            "quantities": "unknown",
            "allergens": "unknown",
            "rights": "unverified",
        },
        "issues": [],
    }


def deduplicate(entries: list[object]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen: dict[tuple[str, tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}
    for index, entry in enumerate(entries, 1):
        record = _require_record(entry, index)
        identity = _identity(record)
        current = {"index": index, "raw_sha256": _raw_sha(record), "entry": record}
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = current
            kept.append(record)
            continue
        dropped.append(
            {
                "name": identity[0],
                "reason": "菜名、规范化食材和步骤相同",
                "kept_index": previous["index"],
                "dropped_index": index,
                "kept_raw_sha256": previous["raw_sha256"],
                "dropped_raw_sha256": current["raw_sha256"],
                "normalized_ingredients": list(identity[1]),
                "normalized_step_count": len(identity[2]),
                "raw_content_differs": previous["raw_sha256"] != current["raw_sha256"],
            }
        )
    return kept, dropped


def cross_source_reviews(
    existing: list[dict[str, Any]], howtocook: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_name: dict[str, list[str]] = {}
    for parent in existing:
        by_name.setdefault(parent["name"], []).append(parent["document_id"])
    reviews = []
    for parent in howtocook:
        matches = by_name.get(parent["name"])
        if not matches:
            continue
        reviews.append(
            {
                "name": parent["name"],
                "existing_document_ids": matches,
                "howtocook_document_id": parent["document_id"],
                "action": "recorded_only",
            }
        )
    return reviews


def load_entries(path: Path) -> list[object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("清洗文件必须是 JSON 数组")
    return data


def enforce_load_contract(result: dict[str, Any]) -> None:
    dropped_names = [item["name"] for item in result["dropped"]]
    expected_drop = dropped_names == ["陈皮排骨汤"]
    if result["input_count"] != 444 or result["kept_count"] != 443 or not expected_drop:
        raise ValueError(f"去重结果不是 444→443（陈皮排骨汤）：{dropped_names}")
    if result["existing_count"] != 40 or len(result["parents"]) != 483:
        raise ValueError("统一父文档不是 40 + 443")
    for parent in result["howtocook"]:
        fields = parent["fields"]
        if fields["servings"] is not None or fields["duration_minutes"] is not None:
            raise ValueError(f"写入了来源没有的份数或用时：{parent['name']}")
        if fields["calories_per_serving"] is not None:
            raise ValueError(f"把 heat 或其他字段写成了热量：{parent['name']}")
        if parent["publishable"] or parent["capabilities"]["publishable_candidate"]:
            raise ValueError(f"待审核菜谱被标成可发布：{parent['name']}")
        if parent["capabilities"]["allergen_hard_filter"]:
            raise ValueError(f"未知过敏原被当成硬过滤：{parent['name']}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"既有父文档第 {line_number} 行不是对象")
        rows.append(row)
    return rows


def build_research_parents(
    entries: list[object],
    existing: list[dict[str, Any]],
    *,
    source_key: str,
    source_sha: str,
) -> dict[str, Any]:
    kept, dropped = deduplicate(entries)
    indexes = {id(entry): index for index, entry in enumerate(entries, 1)}
    howtocook = [
        build_parent(entry, source_key=source_key, source_sha=source_sha, index=indexes[id(entry)])
        for entry in kept
    ]
    reviews = cross_source_reviews(existing, howtocook)
    return {
        "parents": [*existing, *howtocook],
        "howtocook": howtocook,
        "dropped": dropped,
        "reviews": reviews,
        "input_count": len(entries),
        "kept_count": len(kept),
        "existing_count": len(existing),
    }
