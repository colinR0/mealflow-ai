"""Load a checked export into a migrated Mealflow database.

Complete current tables are loaded as-is. Tables that could not be read, and
the two membership tables rebuilt from empty pages, can take rows from the
verified 2026-11-10 snapshot. Confirmed rows are not mixed back into a
complete current table. Vectors are restored from vector_send bytes.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

SKIP = {"auth_sessions", "login_attempts", "alembic_version", "runtime_admission"}
SUPPLEMENT = {"workspace_members", "household_profiles"}
FROM_DUMP = {
    "ingredients",
    "household_invites",
    "inventory_lots",
    "inventory_movements",
    "shopping_items",
    "pending_actions",
    "audit_events",
}
PUBLIC_ORDER = (
    "users",
    "workspaces",
    "ingredients",
    "ingredient_aliases",
    "workspace_members",
    "household_profiles",
    "household_invites",
    "inventory_lots",
    "inventory_movements",
    "shopping_items",
    "turn_sessions",
    "agent_turn_runs",
    "agent_events",
    "tool_executions",
    "pending_actions",
    "background_jobs",
    "idempotency_records",
    "audit_events",
    "recipe_source_assets",
    "recipe_source_asset_versions",
)
DUMP_NOTE = "2026-11-10 00:03 snapshot; later changes cannot be confirmed"


def _quote(table: str) -> str:
    return f'public."{table}"'


def _columns(connection: psycopg.Connection, table: str, schema: str = "public") -> list[str]:
    rows = connection.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    ).fetchall()
    return [row[0] for row in rows]


def _adapt(value):
    if isinstance(value, (dict, list)):
        return Json(value)
    return value


def _copy_binary(
    connection: psycopg.Connection, table: str, path: Path, schema: str = "public"
) -> None:
    qualified = f'"{schema}"."{table}"' if schema != "public" else _quote(table)
    with path.open("rb") as handle, connection.cursor() as cursor:
        with cursor.copy(f"COPY {qualified} FROM STDIN (FORMAT binary)") as copy:
            while block := handle.read(1024 * 1024):
                copy.write(block)


def _insert_from_source(
    source: psycopg.Connection,
    target: psycopg.Connection,
    table: str,
    *,
    only_missing: bool,
    extra: dict | None = None,
) -> tuple[int, list[str]]:
    source_columns = _columns(source, table)
    target_columns = _columns(target, table)
    shared = [name for name in target_columns if name in source_columns or name in (extra or {})]
    if "id" not in shared and table != "runtime_admission":
        return 0, [f"{table}: no shared id column"]
    rows = source.execute(
        "SELECT "
        + ", ".join(name for name in shared if name in source_columns)
        + f" FROM {_quote(table)}"
    ).fetchall()
    selected = [name for name in shared if name in source_columns]
    inserted = 0
    rejected: list[str] = []
    existing = set()
    if only_missing:
        existing = {row[0] for row in target.execute(f"SELECT id FROM {_quote(table)}").fetchall()}
    for row in rows:
        payload = dict(zip(selected, row, strict=True))
        if only_missing and payload["id"] in existing:
            continue
        if table == "pending_actions" and payload.get("status") == "pending":
            payload["status"] = "expired"
        if table == "inventory_lots":
            ingredient_id = target.execute(
                "SELECT id FROM ingredients WHERE key = %s",
                (payload["ingredient_key"],),
            ).fetchone()
            if ingredient_id is None:
                rejected.append(f"inventory_lots {payload['id']}: ingredient key has no row")
                continue
            payload["ingredient_id"] = ingredient_id[0]
        columns = [name for name in target_columns if name in payload or name in (extra or {})]
        values = [_adapt(payload.get(name, (extra or {}).get(name))) for name in columns]
        statement = (
            f"INSERT INTO {_quote(table)} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))})"
        )
        try:
            with target.transaction():
                target.execute(statement, values)
        except psycopg.Error as exc:
            detail = str(exc).splitlines()[0]
            rejected.append(f"{table} {payload.get('id')}: {exc.sqlstate} {detail}")
            continue
        inserted += 1
    return inserted, rejected


def _import_chunks(connection: psycopg.Connection, path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    statement = """
        INSERT INTO recipe_research.chunks (
            id, batch_id, chunk_id, document_id, content_version, section,
            step_ordinals, text, embedding, search_tsv, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::vector, %s::tsvector, %s
        )
    """
    with connection.cursor() as cursor:
        cursor.executemany(
            statement,
            [
                (
                    int(row["id"]),
                    row["batch_id"],
                    row["chunk_id"],
                    row["document_id"],
                    row["content_version"],
                    row["section"],
                    row["step_ordinals"],
                    row["text"],
                    row["embedding_text"],
                    row["search_tsv"] or None,
                    row["created_at"],
                )
                for row in rows
            ],
        )
    return len(rows)


def _vector_check(connection: psycopg.Connection, manifest_path: Path) -> dict:
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = connection.execute(
        """
        SELECT id, chunk_id, document_id, content_version, batch_id,
               md5(vector_send(embedding)), vector_dims(embedding), embedding IS NULL
        FROM recipe_research.chunks
        ORDER BY id
        """
    ).fetchall()
    by_id = {row[0]: row for row in actual}
    mismatches = []
    for item in expected:
        row = by_id.get(item["id"])
        if row is None or row[1:] != (
            item["chunk_id"],
            item["document_id"],
            item["content_version"],
            item["batch_id"],
            item["vector_md5"],
            item["dims"],
            item["is_null"],
        ):
            mismatches.append(item["id"])
    zero = connection.execute(
        """
        SELECT count(*) FROM recipe_research.chunks
        WHERE embedding IS NULL OR vector_dims(embedding) <> 512
           OR NOT EXISTS (
               SELECT 1 FROM unnest(embedding::real[]) AS value WHERE value <> 0
           )
        """
    ).fetchone()[0]
    return {
        "expected": len(expected),
        "actual": len(actual),
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "null_or_zero_or_wrong_dim": int(zero),
    }


def _copy_csv(connection: psycopg.Connection, table: str, path: Path) -> int:
    columns = _columns(connection, table)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return 0
    statement = (
        f"INSERT INTO {_quote(table)} ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})"
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            statement,
            [[_adapt(row[name]) if row[name] != "" else None for name in columns] for row in rows],
        )
    return len(rows)


def import_database(
    target_url: str,
    source_url: str,
    export_dir: Path,
    report_path: Path,
    start_at: str | None = None,
) -> dict:
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    by_name = {item["table"]: item for item in manifest["tables"] if item["schema"] == "public"}
    report: dict = {"tables": [], "vector": None}
    started = start_at is None
    with psycopg.connect(target_url, autocommit=True) as target, psycopg.connect(
        source_url, autocommit=True
    ) as source:
        for table in PUBLIC_ORDER:
            if not started:
                if table == start_at:
                    started = True
                else:
                    continue
            entry = {"table": table}
            info = by_name.get(table, {})
            if table in SKIP:
                entry["disposition"] = "未恢复"
                report["tables"].append(entry)
                continue
            if table in FROM_DUMP:
                count, rejected = _insert_from_source(source, target, table, only_missing=False)
                entry.update(
                    {
                        "disposition": "旧备份恢复",
                        "rows": count,
                        "rejected": rejected,
                        "note": DUMP_NOTE,
                    }
                )
            elif info.get("status") == "current_complete":
                csv_path = export_dir / "tables" / f"public.{table}.csv"
                if csv_path.exists():
                    info_rows = _copy_csv(target, table, csv_path)
                    info = {**info, "row_count": info_rows}
                else:
                    _copy_binary(target, table, export_dir / "tables" / f"public.{table}.bin")
                if table == "background_jobs":
                    target.execute(
                        """
                        UPDATE background_jobs
                        SET status = 'interrupted'
                        WHERE status IN ('queued', 'running')
                        """
                    )
                entry.update(
                    {"disposition": "当前数据恢复", "rows": info.get("row_count"), "rejected": []}
                )
            elif table in SUPPLEMENT:
                _copy_binary(target, table, export_dir / "tables" / f"public.{table}.bin")
                added, rejected = _insert_from_source(source, target, table, only_missing=True)
                entry.update(
                    {
                        "disposition": "当前数据恢复",
                        "rows": info.get("row_count"),
                        "dump_rows_added": added,
                        "rejected": rejected,
                        "note": "当前行来自故障后补入；历史行来自 " + DUMP_NOTE,
                    }
                )
            else:
                entry.update(
                    {
                        "disposition": "未恢复",
                        "error": info.get("message"),
                        "rejected": [],
                    }
                )
            report["tables"].append(entry)
            added = entry.get("dump_rows_added", "-")
            print(f"{table}: {entry['disposition']} rows={entry.get('rows')} added={added}")
        for schema, table in (
            ("recipe_research", "build_batches"),
            ("recipe_research", "parents"),
        ):
            _copy_binary(
                target,
                table,
                export_dir / "tables" / f"{schema}.{table}.bin",
                schema=schema,
            )
            report["tables"].append({"table": f"{schema}.{table}", "disposition": "当前数据恢复"})
        chunk_count = _import_chunks(target, export_dir / "chunks-portable.csv")
        target.execute(
            """
            SELECT setval(
                pg_get_serial_sequence('recipe_research.chunks', 'id'),
                (SELECT max(id) FROM recipe_research.chunks)
            )
            """
        )
        target.execute(
            "DROP INDEX IF EXISTS recipe_research.ix_recipe_research_chunks_embedding_hnsw"
        )
        target.execute(
            """
            CREATE INDEX ix_recipe_research_chunks_embedding_hnsw
            ON recipe_research.chunks USING hnsw (embedding vector_cosine_ops)
            """
        )
        target.execute("REINDEX INDEX recipe_research.ix_recipe_research_chunks_search_tsv")
        report["tables"].append(
            {"table": "recipe_research.chunks", "disposition": "当前数据恢复", "rows": chunk_count}
        )
        report["vector"] = _vector_check(target, export_dir / "chunk-vectors.json")
        parents = target.execute("SELECT count(*) FROM recipe_research.parents").fetchone()[0]
        unchunked = target.execute(
            """
            SELECT document_id FROM recipe_research.parents AS parent
            WHERE NOT EXISTS (
                SELECT 1 FROM recipe_research.chunks AS chunk
                WHERE chunk.document_id = parent.document_id
            )
            """
        ).fetchall()
        report["parents"] = int(parents)
        report["unchunked_parent_ids"] = [row[0] for row in unchunked]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "vector": report["vector"],
        "parents": report["parents"],
        "unchunked": report["unchunked_parent_ids"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start-at")
    args = parser.parse_args(argv)
    try:
        import_database(
            args.target_url, args.source_url, args.export_dir, args.report, args.start_at
        )
    except psycopg.Error as exc:
        detail = str(exc).splitlines()[0]
        print(f"import failed: {exc.sqlstate or type(exc).__name__}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
