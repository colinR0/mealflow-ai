"""Build the unverified HowToCook research parent corpus.

Run from apps/api:
  python -m app.cli.load_howtocook_research

This writes files only. It does not update PostgreSQL or the business recipe table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.cli.register_recipe_sources import ROOT, fingerprint
from app.services.research_recipes import (
    RULE_VERSION,
    assert_source_identity,
    build_research_parents,
    enforce_load_contract,
    load_entries,
    load_jsonl,
)

DEFAULT_SOURCE = (
    ROOT / "data/sources/pending/howtocook-mock-clean/recipes_howtocook_mock_clean.json"
)
DEFAULT_EXISTING = ROOT / "output/prechunk-audit-v1/ready_for_chunking.jsonl"
DEFAULT_OUTPUT = ROOT / "output/howtocook-research-v1"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_output(source: Path, existing_path: Path, output_dir: Path) -> dict[str, Any]:
    source_sha, source_bytes = assert_source_identity(source)
    source_key = source.resolve().relative_to(ROOT).as_posix()
    result = build_research_parents(
        load_entries(source),
        load_jsonl(existing_path),
        source_key=source_key,
        source_sha=source_sha,
    )
    enforce_load_contract(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "parents.jsonl", result["parents"])
    _write_jsonl(output_dir / "dedup.jsonl", result["dropped"])
    _write_jsonl(output_dir / "cross_source_names.jsonl", result["reviews"])
    parents_sha, _size = fingerprint(output_dir / "parents.jsonl")
    summary = {
        "rule_version": RULE_VERSION,
        "source_path": source_key,
        "source_sha256": source_sha,
        "source_bytes": source_bytes,
        "authorization": "unverified",
        "input_count": result["input_count"],
        "kept_count": result["kept_count"],
        "dropped_count": len(result["dropped"]),
        "dropped_names": [item["name"] for item in result["dropped"]],
        "existing_count": result["existing_count"],
        "parent_count": len(result["parents"]),
        "cross_source_name_count": len(result["reviews"]),
        "parents_sha256": parents_sha,
        "howtocook_publishable_count": sum(parent["publishable"] for parent in result["howtocook"]),
        "database_write_performed": False,
        "production_publishable": False,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--existing", type=Path, default=DEFAULT_EXISTING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        summary = build_output(args.source, args.existing, args.output_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"howtocook load failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
