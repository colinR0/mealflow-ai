"""Copy readable tables out of a damaged Mealflow database.

A failed read is recorded and is never treated as an empty table. Vector values
are copied with PostgreSQL binary COPY so float32 bits survive the round trip.
This tool does not embed recipes or recreate retrieval logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import psycopg

# Heaps replaced with empty pages are readable now, but they are not the
# historical contents of those tables.
PARTIAL_TABLES = {
    ("public", "workspace_members"),
    ("public", "household_profiles"),
    ("public", "login_attempts"),
}

# Kept by the recovery target. Everything else that can be read is archive-only.
RUNTIME_TABLES = {
    ("public", "users"),
    ("public", "workspaces"),
    ("public", "workspace_members"),
    ("public", "ingredients"),
    ("public", "ingredient_aliases"),
    ("public", "household_profiles"),
    ("public", "household_invites"),
    ("public", "inventory_lots"),
    ("public", "inventory_movements"),
    ("public", "shopping_items"),
    ("public", "turn_sessions"),
    ("public", "agent_turn_runs"),
    ("public", "agent_events"),
    ("public", "tool_executions"),
    ("public", "pending_actions"),
    ("public", "background_jobs"),
    ("public", "idempotency_records"),
    ("public", "audit_events"),
    ("public", "runtime_admission"),
    ("public", "recipe_source_assets"),
    ("public", "recipe_source_asset_versions"),
    ("recipe_research", "parents"),
    ("recipe_research", "chunks"),
    ("recipe_research", "build_batches"),
}

SKIP_RESTORE = {
    ("public", "auth_sessions"),
    ("public", "login_attempts"),
    ("public", "alembic_version"),
}


def _quote(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _columns(connection: psycopg.Connection, schema: str, table: str) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT column_name, udt_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    ).fetchall()
    return [
        {"name": name, "type": udt, "nullable": nullable}
        for name, udt, nullable in rows
    ]


def _copy_table(connection: psycopg.Connection, schema: str, table: str, path: Path) -> int:
    statement = f"COPY {_quote(schema, table)} TO STDOUT (FORMAT binary)"
    with path.open("wb") as handle, connection.cursor() as cursor:
        with cursor.copy(statement) as copy:
            for block in copy:
                handle.write(block)
    count = connection.execute(f"SELECT count(*) FROM {_quote(schema, table)}").fetchone()
    return int(count[0])


def _vector_manifest(connection: psycopg.Connection, path: Path) -> dict:
    rows = connection.execute(
        """
        SELECT id, chunk_id, document_id, content_version, batch_id,
               vector_dims(embedding), embedding IS NULL,
               md5(vector_send(embedding)),
               CASE
                   WHEN embedding IS NULL THEN NULL
                   ELSE NOT EXISTS (
                       SELECT 1 FROM unnest(embedding::real[]) AS value
                       WHERE value <> 0
                   )
               END
        FROM recipe_research.chunks
        ORDER BY id
        """
    ).fetchall()
    records = []
    for row in rows:
        records.append(
            {
                "id": row[0],
                "chunk_id": row[1],
                "document_id": row[2],
                "content_version": row[3],
                "batch_id": row[4],
                "dims": row[5],
                "is_null": row[6],
                "vector_md5": row[7],
                "is_zero": row[8],
            }
        )
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    missing = connection.execute(
        """
        SELECT p.document_id
        FROM recipe_research.parents AS p
        WHERE NOT EXISTS (
            SELECT 1 FROM recipe_research.chunks AS c
            WHERE c.document_id = p.document_id
        )
        ORDER BY p.document_id
        """
    ).fetchall()
    distinct_parents = connection.execute(
        "SELECT count(DISTINCT document_id) FROM recipe_research.chunks"
    ).fetchone()[0]
    return {
        "chunk_rows": len(records),
        "distinct_parent_documents": int(distinct_parents),
        "unchunked_parent_ids": [item[0] for item in missing],
        "null_vectors": sum(1 for item in records if item["is_null"]),
        "zero_vectors": sum(1 for item in records if item["is_zero"]),
        "nonempty_512": sum(
            1
            for item in records
            if item["dims"] == 512 and not item["is_null"] and not item["is_zero"]
        ),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def export_database(database_url: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    table_dir = output_dir / "tables"
    table_dir.mkdir(exist_ok=True)
    manifest: dict = {"tables": [], "vector": None}
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as connection:
        connection.execute("SET statement_timeout = '120s'")
        names = connection.execute(
            """
            SELECT n.nspname, c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('public', 'recipe_research') AND c.relkind = 'r'
            ORDER BY 1, 2
            """
        ).fetchall()
        for schema, table in names:
            entry = {
                "schema": schema,
                "table": table,
                "columns": _columns(connection, schema, table),
                "role": "runtime"
                if (schema, table) in RUNTIME_TABLES
                else "skip_restore"
                if (schema, table) in SKIP_RESTORE
                else "archive_only",
            }
            path = table_dir / f"{schema}.{table}.bin"
            try:
                count = _copy_table(connection, schema, table, path)
            except psycopg.Error as exc:
                path.unlink(missing_ok=True)
                entry.update(
                    {
                        "status": "unreadable",
                        "error": exc.sqlstate or type(exc).__name__,
                        "message": str(exc).splitlines()[0],
                    }
                )
            else:
                partial = (schema, table) in PARTIAL_TABLES
                entry.update(
                    {
                        "status": "current_partial" if partial else "current_complete",
                        "row_count": count,
                        "sha256": _sha256(path),
                        "bytes": path.stat().st_size,
                    }
                )
            manifest["tables"].append(entry)
            print(f"{schema}.{table}: {entry['status']} rows={entry.get('row_count', '-')}")
        vector_path = output_dir / "chunk-vectors.json"
        manifest["vector"] = _vector_manifest(connection, vector_path)
        manifest["vector"]["path"] = vector_path.name
    destination = output_dir / "manifest.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={destination}")
    print(json.dumps(manifest["vector"], ensure_ascii=False))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        export_database(args.database_url, args.output_dir)
    except psycopg.Error as exc:
        print(f"export failed: {exc.sqlstate or type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
