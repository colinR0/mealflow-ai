"""Install S5 PostgreSQL-only columns and indexes without creating an Alembic head."""

import sys

from sqlalchemy import create_engine

from app.core.config import get_settings
from app.services.native_retrieval import install_native_schema


def main() -> int:
    engine = create_engine(get_settings().database_url)
    with engine.begin() as connection:
        install_native_schema(connection)
    print("S5 native schema installed (pgvector + Chinese-tokenized tsvector).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
