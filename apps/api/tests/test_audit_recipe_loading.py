"""File-mode tests for pre-chunk recipe loading audit.

These tests do not need PostgreSQL. Run with:
  python -m pytest tests/test_audit_recipe_loading.py --noconftest -q
because apps/api/tests/conftest.py requires MEALFLOW_TEST_DATABASE_URL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import audit_recipe_loading as audit
from app.cli.register_recipe_sources import fingerprint


@pytest.fixture()
def fixture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "data/seed").mkdir(parents=True)
    (root / "apps/web/public/data").mkdir(parents=True)
    (root / "output/recipe-loading-v1/md").mkdir(parents=True)
    (root / "output/mineru-batch-v1").mkdir(parents=True)
    (root / "data/sources/pending/ext").mkdir(parents=True)
    monkeypatch.setattr(audit, "ROOT", root)
    monkeypatch.setattr(audit, "SOURCE_JSON", "data/seed/recipes.json")
    monkeypatch.setattr(audit, "LEGACY_JSON", "apps/web/public/data/recipes_merged.json")
    return root


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    digest, _ = fingerprint(path)
    return digest


def _registry(records: list[dict]) -> dict:
    return {"mode": "dry_run", "records": records}


def test_hash_mismatch_and_missing_parse_do_not_stop_other_sources(
    fixture_root: Path, tmp_path: Path
):
    seed = {
        "recipes": [
            {
                "name": "测试炒蛋",
                "servings": 2,
                "duration_minutes": 10,
                "kcal_per_serving": 100,
                "estimated_cost_cents": 100,
                "tags": ["测试"],
                "allergens": ["蛋"],
                "steps": ["打蛋", "炒熟"],
                "source_name": "fixture",
                "source_url": "",
                "license_note": "test",
                "ingredients": [{"key": "egg", "quantity": 2, "unit": "个", "optional": False}],
            }
        ]
    }
    seed_path = fixture_root / "data/seed/recipes.json"
    seed_hash = _write(seed_path, json.dumps(seed, ensure_ascii=False))
    legacy_path = fixture_root / "apps/web/public/data/recipes_merged.json"
    legacy_hash = _write(
        legacy_path,
        json.dumps(
            [{"name": "旧菜", "stuff": "土豆", "bv": "BVTEST1", "steps": [], "source": None}],
            ensure_ascii=False,
        ),
    )

    md = """# 虾仁豆腐蒸蛋
菜谱ID：rload-004
版本：1
菜名：虾仁豆腐蒸蛋
类别：蒸菜
基准份数：3 人份
准备时间：18 分钟
烹饪时间：16 分钟
预计总时间：34 分钟
设备：蒸锅
过敏原与核对：虾、蛋
## 食材（基于上述份数）
- 虾仁 150 g
- 鸡蛋 3 个
## 做法步骤
1. 准备。
2. 蒸熟。
完成判断：熟透。
来源：fixture
使用说明：test
"""
    md_path = fixture_root / "output/recipe-loading-v1/md/rload-004.md"
    md_hash = _write(md_path, md)

    pdf_key = "output/pdf/recipe-loading-v1/rload-001.pdf"
    pdf_path = fixture_root / pdf_key
    pdf_hash = _write(pdf_path, "%PDF-fixture")
    # Intentionally do not create mineru parse artifact.

    broken_key = "output/recipe-loading-v1/md/rload-005.md"
    broken_path = fixture_root / broken_key
    broken_body = md.replace("rload-004", "rload-005").replace("虾仁豆腐蒸蛋", "西兰花蒜香牛肉")
    broken_hash = _write(broken_path, broken_body)

    manifest = {
        "recipes": [
            {
                "id": "rload-001",
                "name": "姜葱清蒸鲈鱼",
                "format": "pdf",
                "path": pdf_key,
                "sha256": pdf_hash,
                "source": "fixture",
                "version": 1,
            },
            {
                "id": "rload-004",
                "name": "虾仁豆腐蒸蛋",
                "format": "md",
                "path": "output/recipe-loading-v1/md/rload-004.md",
                "sha256": md_hash,
                "source": "fixture",
                "version": 1,
            },
            {
                "id": "rload-005",
                "name": "西兰花蒜香牛肉",
                "format": "md",
                "path": broken_key,
                "sha256": "0" * 64,  # force manifest hash mismatch
                "source": "fixture",
                "version": 1,
            },
        ]
    }
    manifest_path = fixture_root / "output/recipe-loading-v1/manifest.json"
    _write(manifest_path, json.dumps(manifest, ensure_ascii=False))

    # External pending with image alt mismatch and discontinuous steps omitted.
    ext_md = """# 外部菜
### 用料
土豆
1个/2个
猪肉沫
150克
### 做法步骤
步骤 1
![纯奶手撕吐司的做法 步骤1](http://example.com/a.jpg)
切土豆
步骤 2
![纯奶手撕吐司的做法 步骤1](http://example.com/b.jpg)
炒肉
"""
    ext_pdf = fixture_root / "data/sources/pending/ext/source.pdf"
    ext_pdf_hash = _write(ext_pdf, "%PDF-ext")
    ext_full = fixture_root / "data/sources/pending/ext/full.md"
    ext_md_hash = _write(ext_full, ext_md)
    ext_manifest = {
        "source_id": "ext-001",
        "title": "外部菜",
        "rights_status": "unverified",
        "attribution_in_document": "unknown",
        "files": [
            {"role": "original", "path": "source.pdf", "sha256": ext_pdf_hash},
            {"role": "parsed_markdown", "path": "full.md", "sha256": ext_md_hash},
        ],
    }
    _write(
        fixture_root / "data/sources/pending/ext/manifest.json",
        json.dumps(ext_manifest, ensure_ascii=False),
    )

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            _registry(
                [
                    {
                        "source_key": "data/seed/recipes.json",
                        "format": "json",
                        "sha256": seed_hash,
                        "size_bytes": 1,
                        "result": "scanned",
                    },
                    {
                        "source_key": "apps/web/public/data/recipes_merged.json",
                        "format": "json",
                        "sha256": legacy_hash,
                        "size_bytes": 1,
                        "result": "scanned",
                    },
                    {
                        "source_key": pdf_key,
                        "format": "pdf",
                        "sha256": pdf_hash,
                        "size_bytes": 1,
                        "result": "scanned",
                    },
                    {
                        "source_key": "output/recipe-loading-v1/md/rload-004.md",
                        "format": "md",
                        "sha256": md_hash,
                        "size_bytes": 1,
                        "result": "scanned",
                    },
                    {
                        "source_key": broken_key,
                        "format": "md",
                        "sha256": broken_hash,
                        "size_bytes": 1,
                        "result": "scanned",
                    },
                    {
                        "source_key": "data/sources/pending/ext/source.pdf",
                        "format": "pdf",
                        "sha256": ext_pdf_hash,
                        "size_bytes": 1,
                        "result": "scanned",
                    },
                ]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out = tmp_path / "out"
    summary = audit.run_audit(
        registry_path=registry_path,
        inputs=None,
        manifest_path=manifest_path,
        parsed_pdf_dir=fixture_root / "output/mineru-batch-v1",
        pending_root=fixture_root / "data/sources/pending",
        output_dir=out,
        rule_version="test-rules",
        parser_version="test-parser",
        seed=1,
        sample_size=20,
        dry_run=False,
        force=True,
    )
    parents = [
        json.loads(line)
        for line in (out / "parents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    issues = [
        json.loads(line)
        for line in (out / "issues.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    codes = {item["code"] for item in issues}
    assert "PARSED_ARTIFACT_MISSING" in codes
    assert "MANIFEST_HASH_MISMATCH" in codes
    assert "IMAGE_ALT_MISMATCH" in codes
    assert "RIGHTS_UNVERIFIED" in codes
    assert any(p["document_id"] == "rload-004" for p in parents)
    sample = next(p for p in parents if p["document_id"] == "rload-004")
    assert sample["fields"]["allergen_note"] == "虾、蛋"
    assert any(p["source_kind"] == "seed" for p in parents)
    assert any(p["source_kind"] == "legacy" for p in parents)
    external = next(p for p in parents if p["source_kind"] == "external")
    assert external["publishable"] is False
    assert external["status"] == "needs_review"
    assert summary["database_write_performed"] is False

    # Idempotent document ids on rerun.
    summary2 = audit.run_audit(
        registry_path=registry_path,
        inputs=None,
        manifest_path=manifest_path,
        parsed_pdf_dir=fixture_root / "output/mineru-batch-v1",
        pending_root=fixture_root / "data/sources/pending",
        output_dir=out,
        rule_version="test-rules",
        parser_version="test-parser",
        seed=1,
        sample_size=20,
        dry_run=False,
        force=True,
    )
    parents2 = [
        json.loads(line)
        for line in (out / "parents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [p["document_id"] for p in parents] == [p["document_id"] for p in parents2]
    assert summary["manual_review_seed"] == summary2["manual_review_seed"]

    # An unchanged run must preserve the complete corpus, not emit only changed files.
    summary3 = audit.run_audit(
        registry_path=registry_path,
        inputs=None,
        manifest_path=manifest_path,
        parsed_pdf_dir=fixture_root / "output/mineru-batch-v1",
        pending_root=fixture_root / "data/sources/pending",
        output_dir=out,
        rule_version="test-rules",
        parser_version="test-parser",
        seed=1,
        sample_size=20,
        dry_run=False,
        force=False,
    )
    parents3 = [
        json.loads(line)
        for line in (out / "parents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ready = [
        json.loads(line)
        for line in (out / "ready_for_chunking.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    excluded = [
        json.loads(line)
        for line in (out / "excluded.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [p["document_id"] for p in parents] == [p["document_id"] for p in parents3]
    assert summary3["sample_manifest_parent_count_ok"] is True
    assert summary3["ready_for_chunking_count"] == len(ready)
    assert summary3["excluded_count"] == len(excluded)
    assert len(ready) + len(excluded) == len(parents3)
    assert all(item["source_kind"] in {"seed", "sample"} for item in ready)
    assert any(item["source_record_key"] == "ext-001" for item in excluded)


def test_discontinuous_steps_marked_invalid():
    steps, ok = audit.parse_numbered_steps("1. a\n3. b\n")
    assert ok is False
    assert [s["ordinal"] for s in steps] == [1, 3]


def test_commit_db_switch_rejected():
    with pytest.raises(SystemExit):
        audit.main(["--commit-db"])
