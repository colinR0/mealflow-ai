"""Discover recipe source files and register changed bytes for later parsing.

Run from apps/api with ``python -m app.cli.register_recipe_sources``. The
default action is read-only; ``--commit`` requires the configured PostgreSQL
database to be at the current Alembic head.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SUPPORTED_SUFFIXES = {".json", ".pdf", ".md", ".txt"}
HASH_BUFFER_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Candidate:
    path: Path
    source_key: str
    format: str


def default_inputs() -> list[Path]:
    """Scan only source locations; parsed MinerU output is a derived artifact."""
    inputs = [
        ROOT / "data/seed/recipes.json",
        ROOT / "apps/web/public/data/recipes_merged.json",
        ROOT / "output/pdf/recipe-loading-v1",
        ROOT / "output/recipe-loading-v1/md",
        ROOT / "output/recipe-loading-v1/txt",
    ]
    pending = ROOT / "data/sources/pending"
    if pending.exists():
        inputs.extend(sorted(pending.rglob("source.pdf")))
    return inputs


def discover(inputs: list[Path]) -> list[Candidate]:
    candidates: dict[str, Candidate] = {}
    for item in inputs:
        path = item if item.is_absolute() else ROOT / item
        if not path.exists():
            raise FileNotFoundError(f"来源路径不存在：{path}")
        paths = path.rglob("*") if path.is_dir() else (path,)
        for found in paths:
            if not found.is_file() or found.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            resolved = found.resolve(strict=True)
            if not resolved.is_relative_to(ROOT):
                raise ValueError(f"来源文件不在项目目录内：{found}")
            source_key = resolved.relative_to(ROOT).as_posix()
            if len(source_key) > 1024:
                raise ValueError(f"来源路径超过数据库字段长度：{source_key}")
            candidates[source_key] = Candidate(resolved, source_key, found.suffix.lower()[1:])
    if not candidates:
        raise ValueError("输入路径中没有受支持的来源文件")
    return [candidates[key] for key in sorted(candidates)]


def fingerprint(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(HASH_BUFFER_BYTES), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"计算摘要时文件发生变化：{path}")
    return digest.hexdigest(), after.st_size


def register(candidate: Candidate, sha256: str, size_bytes: int) -> tuple[str, int]:
    """Serialize revisions by locking one asset row; each file is one transaction."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from app.db.models import new_id, utc_now
    from app.db.session import SessionLocal
    from app.db.source_registry_models import RecipeSourceAsset, RecipeSourceAssetVersion

    with SessionLocal.begin() as session:
        now = utc_now()
        session.execute(
            insert(RecipeSourceAsset)
            .values(
                id=new_id(),
                source_key=candidate.source_key,
                format=candidate.format,
                current_version=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[RecipeSourceAsset.source_key])
        )
        asset = session.execute(
            select(RecipeSourceAsset)
            .where(RecipeSourceAsset.source_key == candidate.source_key)
            .with_for_update()
        ).scalar_one()
        if asset.format != candidate.format:
            raise ValueError(f"已登记格式与当前文件不一致：{candidate.source_key}")
        if asset.current_version:
            current = session.execute(
                select(RecipeSourceAssetVersion).where(
                    RecipeSourceAssetVersion.asset_id == asset.id,
                    RecipeSourceAssetVersion.version == asset.current_version,
                )
            ).scalar_one()
            if current.sha256 == sha256:
                return "skipped", asset.current_version
        next_version = asset.current_version + 1
        session.add(
            RecipeSourceAssetVersion(
                asset_id=asset.id,
                version=next_version,
                sha256=sha256,
                size_bytes=size_bytes,
                status="discovered",
            )
        )
        asset.current_version = next_version
        return ("registered" if next_version == 1 else "updated"), next_version


def scan(inputs: list[Path], *, commit: bool) -> dict:
    candidates = discover(inputs)
    if commit:
        # Never write source state against a database with pending migrations.
        from app.db.schema_guard import DatabaseSchemaOutdatedError, ensure_schema_current

        try:
            ensure_schema_current()
        except DatabaseSchemaOutdatedError as exc:
            raise ValueError(
                "来源登记未写入：数据库未到当前迁移头。"
                "先核对 DATABASE_URL、alembic current 和 alembic heads。"
            ) from exc
    records = []
    counts: Counter[str] = Counter()
    for candidate in candidates:
        try:
            sha256, size_bytes = fingerprint(candidate.path)
            result, version = (
                register(candidate, sha256, size_bytes) if commit else ("scanned", None)
            )
            records.append(
                {
                    "source_key": candidate.source_key,
                    "format": candidate.format,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                    "result": result,
                    "version": version,
                }
            )
            counts[result] += 1
        except (OSError, ValueError) as exc:
            # One unreadable/changing file must not hide the status of other sources.
            records.append(
                {"source_key": candidate.source_key, "result": "error", "error": str(exc)}
            )
            counts["error"] += 1
    return {
        "mode": "commit" if commit else "dry_run",
        "source_files": len(candidates),
        "counts": dict(sorted(counts.items())),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="登记菜谱来源文件及其内容版本")
    parser.add_argument(
        "--input", action="append", type=Path, dest="inputs", help="文件或目录，可重复指定"
    )
    parser.add_argument("--commit", action="store_true", help="写入已迁移的 PostgreSQL 来源表")
    parser.add_argument("--report", type=Path, help="保存完整 JSON 扫描报告")
    args = parser.parse_args()
    try:
        inputs = args.inputs if args.inputs is not None else default_inputs()
        report = scan(inputs, commit=args.commit)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    summary = {key: value for key, value in report.items() if key != "records"}
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if report["counts"].get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
