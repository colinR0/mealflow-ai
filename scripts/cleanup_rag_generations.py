"""Remove retired RAG generations while keeping the active index and recent history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.db.schema_guard import ensure_schema_current
from app.db.session import engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean old RAG index generations.")
    parser.add_argument(
        "--keep", type=int, default=1,
        help="number of inactive ready generations to retain (default: 1)",
    )
    parser.add_argument("--apply", action="store_true", help="actually delete rows")
    args = parser.parse_args()
    if args.keep < 0:
        raise SystemExit("--keep must be non-negative")

    ensure_schema_current()
    with engine.begin() as connection:
        active = connection.execute(text("""
            SELECT id, profile_id FROM index_generations
            WHERE active = true AND status = 'ready'
        """)).mappings().all()
        if len(active) != 1:
            raise SystemExit("expected exactly one active ready generation")
        retired = connection.execute(text("""
            SELECT id, profile_id, created_at FROM index_generations
            WHERE active = false AND status = 'ready'
            ORDER BY created_at DESC, id DESC
        """)).mappings().all()
        remove = [row for row in retired[args.keep:]]
        chunks = {
            row["id"]: int(connection.scalar(
                text("SELECT count(*) FROM knowledge_chunks WHERE generation_id = :id"),
                {"id": row["id"]},
            ) or 0)
            for row in remove
        }
        report = {
            "active": [dict(row) for row in active],
            "kept": [dict(row) for row in retired[:args.keep]],
            "remove": [dict(row) for row in remove],
            "chunks": chunks,
            "applied": args.apply,
        }
        if args.apply and remove:
            for row in remove:
                connection.execute(text(
                    "DELETE FROM knowledge_chunks WHERE generation_id = :id"
                ), {"id": row["id"]})
                connection.execute(text(
                    "DELETE FROM index_generations WHERE id = :id"
                ), {"id": row["id"]})
            # Profiles become unreachable after their generations are removed.
            connection.execute(text("""
                DELETE FROM embedding_profiles p
                WHERE NOT EXISTS (
                    SELECT 1 FROM index_generations g WHERE g.profile_id = p.id
                )
            """))
        print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()