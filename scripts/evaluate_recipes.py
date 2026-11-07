"""Read-only quality audit for the repository's recipe JSON files.

This script assesses source records, not database rows or retrieval quality. It never
grants publication approval: rights and factual accuracy require human review.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = (
    ROOT / "data/seed/recipes.json",
    ROOT / "apps/web/public/data/recipes_merged.json",
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
        parsed = urlparse(value)
    except ValueError:  # Keep a malformed URL as a per-record finding.
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def name_key(value: str) -> str:
    return re.sub(r"[\s（）()·•,，、]+", "", value).casefold()


def load(path: Path) -> tuple[str, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("recipes"), list):
        kind, rows = "seed", payload["recipes"]
    elif isinstance(payload, list):
        kind, rows = "legacy", payload
    else:
        raise ValueError("expected a recipe array or an object with a recipes array")
    return kind, rows


def assess(path: Path, kind: str, index: int, recipe: object) -> dict:
    issues: list[str] = []
    if not isinstance(recipe, dict):
        recipe = {}
        issues.append("INVALID_RECORD")
    name = recipe.get("name")
    if not nonempty(name):
        issues.append("NAME_MISSING")
        name = ""

    steps = recipe.get("steps")
    if not isinstance(steps, list) or any(not nonempty(step) for step in steps):
        issues.append("STEPS_INVALID")
        has_steps = False
    else:
        has_steps = bool(steps)
        if not has_steps:
            issues.append("STEPS_MISSING")

    if kind == "seed":
        ingredients = recipe.get("ingredients")
        has_ingredients = isinstance(ingredients, list) and bool(ingredients)
        quantities_known = has_ingredients
        if has_ingredients:
            for item in ingredients:
                if not isinstance(item, dict) or not nonempty(item.get("key")):
                    has_ingredients = quantities_known = False
                    issues.append("INGREDIENT_INVALID")
                    break
                if not positive_number(item.get("quantity")) or not nonempty(item.get("unit")):
                    quantities_known = False
        if not has_ingredients and "INGREDIENT_INVALID" not in issues:
            issues.append("INGREDIENTS_MISSING")
        if has_ingredients and not quantities_known:
            issues.append("QUANTITY_INVALID_OR_MISSING")
        servings_known = positive_number(recipe.get("servings"))
        if not servings_known:
            issues.append("SERVINGS_INVALID_OR_MISSING")
        if not positive_number(recipe.get("duration_minutes")):
            issues.append("DURATION_INVALID_OR_MISSING")
        for field in ("kcal_per_serving", "estimated_cost_cents"):
            value = recipe.get(field)
            if value is not None and not nonnegative_number(value):
                issues.append("NUTRITION_OR_COST_INVALID")
        allergens = recipe.get("allergens")
        if not isinstance(allergens, list) or any(not nonempty(value) for value in allergens):
            issues.append("ALLERGENS_INVALID")
        if not nonempty(recipe.get("source_name")) or not nonempty(recipe.get("license_note")):
            issues.append("SOURCE_OR_LICENSE_MISSING")
        if nonempty(recipe.get("source_url")) and not web_url(recipe["source_url"]):
            issues.append("SOURCE_URL_INVALID")
        source = recipe.get("source_url") or recipe.get("source_name") or ""
    else:
        has_ingredients = nonempty(recipe.get("stuff"))
        if not has_ingredients:
            issues.append("INGREDIENTS_MISSING")
        # Legacy stuff is a free-text ingredient list. It has no verified quantities.
        quantities_known = servings_known = False
        if has_ingredients:
            issues.append("QUANTITY_UNKNOWN")
        if not nonempty(recipe.get("source")) and not nonempty(recipe.get("bv")):
            issues.append("SOURCE_MISSING")
        if nonempty(recipe.get("source")) and not web_url(recipe["source"]):
            issues.append("SOURCE_URL_INVALID")
        issues.append("RIGHTS_UNVERIFIED")
        source = recipe.get("source") or recipe.get("bv") or ""

    return {
        "file": str(path),
        "row": index + 1,
        "name": name,
        "source": source,
        "kind": kind,
        "issues": issues,
        "capabilities": {
            "discover_candidate": bool(name and has_ingredients),
            "explain_steps_candidate": bool(name and has_ingredients and has_steps),
            "calculate_quantity_candidate": bool(
                name and has_ingredients and quantities_known and servings_known
            ),
        },
        "rights_status": "requires_human_review",
    }


def select_samples(items: list[dict], size: int, seed: int) -> list[dict]:
    """Include distinct risk groups before a reproducible random fill."""
    rng = random.Random(seed)
    selected: list[dict] = []
    seen: set[tuple[str, int]] = set()
    groups = (
        lambda x: x["kind"] == "seed",
        lambda x: "STEPS_MISSING" in x["issues"],
        lambda x: x["capabilities"]["explain_steps_candidate"] and x["kind"] == "legacy",
        lambda x: "POSSIBLE_DUPLICATE_NAME" in x["issues"],
        lambda x: any(code.endswith("INVALID") or code == "INVALID_RECORD" for code in x["issues"]),
    )
    for predicate in groups:
        candidates = [x for x in items if predicate(x)]
        rng.shuffle(candidates)
        for item in candidates[: min(3, max(0, size - len(selected)))]:
            key = (item["file"], item["row"])
            if key not in seen:
                selected.append(item)
                seen.add(key)
    remaining = [x for x in items if (x["file"], x["row"]) not in seen]
    rng.shuffle(remaining)
    return selected + remaining[: max(0, size - len(selected))]


def evaluate(paths: list[Path], sample_size: int, seed: int) -> tuple[dict, list[dict], list[dict]]:
    items: list[dict] = []
    for path in paths:
        kind, rows = load(path)
        items.extend(assess(path, kind, index, row) for index, row in enumerate(rows))

    names: dict[str, list[dict]] = {}
    for item in items:
        if item["name"]:
            names.setdefault(name_key(item["name"]), []).append(item)
    for group in names.values():
        if len(group) > 1:
            for item in group:
                item["issues"].append("POSSIBLE_DUPLICATE_NAME")

    issue_counts = Counter(code for item in items for code in item["issues"])
    capability_counts = Counter(
        key for item in items for key, enabled in item["capabilities"].items() if enabled
    )
    summary = {
        "scope": "JSON source files only; database contents and rights were not verified",
        "total": len(items),
        "by_file": dict(Counter(item["file"] for item in items)),
        "issue_counts": dict(sorted(issue_counts.items())),
        "capability_candidate_counts": dict(sorted(capability_counts.items())),
        "possible_duplicate_name_groups": sum(len(group) > 1 for group in names.values()),
        "manual_sample_size": min(sample_size, len(items)),
        "manual_sample_seed": seed,
    }
    return summary, items, select_samples(items, sample_size, seed)


def write_reports(directory: Path, summary: dict, items: list[dict], samples: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (directory / "items.jsonl").open("w", encoding="utf-8") as output:
        for item in items:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (directory / "manual_review.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=(
                "file", "row", "name", "source", "issues", "source_matches",
                "ingredients_match", "steps_match", "allergen_review", "rights_evidence",
                "reviewer", "review_date", "notes",
            ),
        )
        writer.writeheader()
        for item in samples:
            writer.writerow({
                "file": item["file"], "row": item["row"], "name": item["name"],
                "source": item["source"], "issues": ";".join(item["issues"]),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path, help="JSON files (default: seed + merged)")
    parser.add_argument("--report-dir", type=Path, help="write summary, item results and review CSV")
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261107)
    args = parser.parse_args()
    if args.sample_size < 0:
        parser.error("--sample-size must be nonnegative")
    paths = [path.resolve() for path in (args.inputs or DEFAULT_INPUTS)]
    try:
        summary, items, samples = evaluate(paths, args.sample_size, args.seed)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"recipe audit failed: {error}", file=sys.stderr)
        return 2
    if args.report_dir:
        write_reports(args.report_dir, summary, items, samples)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
