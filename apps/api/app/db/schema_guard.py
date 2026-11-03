"""启动前数据库版本保护，避免旧数据库以模糊 OperationalError 进入业务层。"""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.config import REPO_ROOT
from app.db.session import engine


class DatabaseSchemaOutdatedError(RuntimeError):
    pass


def schema_version_status() -> tuple[str | None, set[str]]:
    config = Config(str(REPO_ROOT / "apps" / "api" / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "apps" / "api" / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    return current, set(scripts.get_heads())


def ensure_schema_current() -> None:
    # Disposable PostgreSQL test schemas use Base.metadata and have no Alembic history.
    if engine.url.database is None:
        return
    current, heads = schema_version_status()
    if current in heads:
        return
    expected = ", ".join(sorted(heads)) or "<无迁移头>"
    raise DatabaseSchemaOutdatedError(
        f"数据库结构未升级：当前 {current or '<未初始化>'}，需要 {expected}。"
        "请在 apps/api 目录执行 `../../.venv/Scripts/alembic.exe upgrade head` 后重启服务。"
    )
