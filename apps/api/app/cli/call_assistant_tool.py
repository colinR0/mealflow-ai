"""Call one independent assistant tool without invoking an agent or generation model."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.cli.register_recipe_sources import ROOT
from app.core.config import get_settings
from app.tools.assistant import AssistantTools, ToolContext


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor-id", required=True, help="本地开发可信用户身份")
    parser.add_argument("--household-id", required=True)
    parser.add_argument("--tool", help="不填写时输出六个工具的声明")
    parser.add_argument("--arguments-file", type=Path, help="UTF-8 JSON 参数文件")
    args = parser.parse_args(argv)
    if args.tool and args.arguments_file is None:
        parser.error("调用工具需要 --arguments-file")
    if args.arguments_file and not args.tool:
        parser.error("--arguments-file 必须与 --tool 一起使用")
    settings = get_settings()
    try:
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        )
    except (SQLAlchemyError, ImportError):
        # Initialization errors must not print a driver exception containing a connection URL.
        print("tool invocation failed: DATABASE_CONFIG", file=sys.stderr)
        return 2
    try:
        model = Path(settings.research_embedding_model_dir)
        tools = AssistantTools(
            ToolContext(
                actor_id=args.actor_id,
                household_id=args.household_id,
                sessions=sessionmaker(bind=engine, expire_on_commit=False),
                batch_id=settings.research_retrieval_batch_id,
                model_dir=model if model.is_absolute() else ROOT / model,
            )
        )
        if args.tool is None:
            output = tools.declarations()
            code = 0
        else:
            arguments = args.arguments_file.read_text(encoding="utf-8-sig")
            result = asyncio.run(tools.call(args.tool, arguments))
            output = result.model_dump(mode="json")
            code = 2 if result.status in {"error", "unavailable"} else 0
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return code
    except (OSError, ValueError) as exc:
        print(f"tool invocation failed: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
