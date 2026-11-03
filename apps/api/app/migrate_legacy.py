"""Run from apps/api: python -m app.migrate_legacy --help."""
import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.services.legacy_migration import migrate, read_source


def main():
    parser = argparse.ArgumentParser(description="Read-only SQLite to v2 explicit import/reconcile")
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument(
        "--source", required=True, help="Stable source identity; reuse on every run",
    )
    parser.add_argument("--source-household", required=True)
    parser.add_argument("--target-household", required=True)
    parser.add_argument("--actor", required=True, help="Existing target household owner account ID")
    parser.add_argument("--reconcile", action="store_true", help="Report only; never import")
    args = parser.parse_args()
    rows = read_source(args.sqlite, args.source_household)
    with SessionLocal.begin() as session:
        report = migrate(session, rows, source=args.source, workspace_id=args.target_household,
                         actor_id=args.actor, reconcile=args.reconcile)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
