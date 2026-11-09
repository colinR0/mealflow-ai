"""Import Markdown cooking skill documents into the shared knowledge index."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db.session import SessionLocal
from app.db.v2_models import KnowledgeDocument, KnowledgeSource
from import_howtocook_rag import ensure_generation, prepare_import

SOURCE_ID = str(uuid5(NAMESPACE_URL, "mealflow:cooking-skills:source:v1"))


def document_id(path: Path) -> str:
    return str(uuid5(NAMESPACE_URL, "mealflow:cooking-skills:" + path.as_posix()))


def parse_markdown(path: Path) -> tuple[str, dict, str]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    title = path.stem
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip() or title
            break
    headings: list[str] = []
    indexed: list[str] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level, heading = len(match[1]), match[2].strip()
            headings = headings[: max(0, level - 1)] + [heading]
            indexed.append(" > ".join(headings))
        elif line.strip():
            indexed.append(line.strip())
    content = {"title": title, "document_type": "cooking_skill", "markdown": raw,
               "sections": indexed, "source_name": "HowToCook 技能库"}
    return title, content, " ".join([title, *indexed])


def build_plan(source: Path) -> list[dict]:
    source = source.resolve()
    paths = sorted(source.rglob("*.md")) if source.is_dir() else [source]
    if not paths:
        raise SystemExit("No Markdown files found")
    base = source if source.is_dir() else source.parent
    # Keep HowToCook identities stable for imports of tips, its subfolders or one file.
    base = next((parent for parent in (base, *base.parents) if parent.name == "tips"), base)
    plan = []
    for path in paths:
        title, content, search_text = parse_markdown(path)
        relative = path.relative_to(base).as_posix()
        content["source_path"] = relative
        if not content["markdown"].strip():
            raise ValueError(f"Empty Markdown: {path}")
        plan.append({"id": document_id(Path(relative)), "title": title,
                     "source_path": relative, "content": content, "search_text": search_text})
    return plan


def apply_plan(plan: list[dict]) -> dict:
    # Reuse the recipe importer's database/profile/schema checks and index reuse logic.
    prepare_import()
    counts = {"created": 0, "updated": 0}
    with SessionLocal.begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('cooking-skills-import'))"))
        source = session.get(KnowledgeSource, SOURCE_ID)
        if source is None:
            session.add(KnowledgeSource(id=SOURCE_ID, name="HowToCook 技能库",
                                        approval="approved", license_note="用户提供的本地 Markdown 技能库",
                                        url=None))
        elif source.approval != "approved":
            raise RuntimeError("Skill source requires approval before import")
        session.flush()
        for item in plan:
            doc = session.get(KnowledgeDocument, item["id"])
            if doc is None:
                session.add(KnowledgeDocument(id=item["id"], recipe_id=None, source_id=SOURCE_ID,
                    document_type="cooking_skill", title=item["title"], source_path=item["source_path"],
                    published=True, content_version=1, capabilities={"markdown": True},
                    content=item["content"], search_text=item["search_text"]))
                counts["created"] += 1
            elif doc.content != item["content"] or doc.search_text != item["search_text"]:
                doc.content_version += 1
                doc.title, doc.source_path = item["title"], item["source_path"]
                doc.content, doc.search_text = item["content"], item["search_text"]
                counts["updated"] += 1
    return {**counts, "generation_id": ensure_generation()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = build_plan(args.source)
    result = {"documents": len(plan), "titles": [item["title"] for item in plan]}
    if args.apply:
        result["import"] = apply_plan(plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
