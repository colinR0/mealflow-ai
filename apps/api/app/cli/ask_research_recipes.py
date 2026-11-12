"""RAG 问答自测 Demo：两步链「检索 → 拼接证据 → 大模型生成」。

Run from apps/api:
  python -m app.cli.ask_research_recipes --query "红烧肉怎么做？"
  python -m app.cli.ask_research_recipes            # 交互模式，空行退出

检索走 recipe_research 研发索引（pgvector，只读，不写库）；生成模型和密钥
来自 .env 的 BIGMODEL_* 配置。答案只允许基于检索到的父菜谱原文。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.cli.register_recipe_sources import ROOT
from app.core.config import get_settings
from app.services.research_index import BgeEmbedder, make_engine, search_parents

DEFAULT_MODEL = ROOT / "models/bge-small-zh-v1.5"
TOP_PARENTS = 3  # 送入提示词的父菜谱数量；太多会稀释证据且增加费用
CHUNK_LIMIT = 8  # 先取 8 个切块，按父菜谱去重后保留前 TOP_PARENTS 道

PROMPT = """你是“智慧冰箱”的菜谱助手。只依据下面的检索资料回答用户问题。
要求：
- 资料足够时，依据原文回答，并注明引用的菜名。
- 资料不足以回答时，直接说不知道，不要编造食材、用量或步骤。
- 原文未标注份数或过敏原时，说明无法精确换算或做安全承诺。

检索资料：
{context}

用户问题：{question}
"""


def latest_ready_batch(connection: Connection) -> str:
    row = connection.execute(
        text(
            "SELECT id FROM recipe_research.build_batches "
            "WHERE status = 'ready_for_evaluation' AND production_publishable = false "
            "ORDER BY created_at DESC LIMIT 1"
        )
    ).first()
    if row is None:
        raise RuntimeError("没有 ready_for_evaluation 的研发批次，请先完成嵌入入库")
    return str(row[0])


def retrieve(
    connection: Connection, embedder: BgeEmbedder, batch_id: str, query: str
) -> list[dict[str, Any]]:
    vector = embedder.embed_queries([query])[0]
    hits = search_parents(
        connection, batch_id=batch_id, query_vector=vector, query=query,
        limit=CHUNK_LIMIT, mode="vector",
    )
    # 做法类问题优先回源完整父菜谱，切块只用于定位和展示命中区段
    parents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        doc_id = str(hit["document_id"])
        if doc_id in seen or len(parents) >= TOP_PARENTS:
            continue
        seen.add(doc_id)
        payload = connection.execute(
            text("SELECT payload FROM recipe_research.parents WHERE document_id = :id"),
            {"id": doc_id},
        ).scalar_one()
        parents.append({**hit, "payload": payload})
    return parents


def build_context(parents: list[dict[str, Any]]) -> str:
    blocks = []
    for index, parent in enumerate(parents, 1):
        blocks.append(
            f"[资料{index}] 菜名：{parent['name']}\n"
            f"命中区段：{parent['section']}（相似度 {float(parent['cosine']):.4f}）\n"
            f"原文：{json.dumps(parent['payload'], ensure_ascii=False)}"
        )
    return "\n\n".join(blocks) if blocks else "（没有检索到任何资料）"


def generate(question: str, context: str) -> str:
    from openai import OpenAI

    settings = get_settings()
    if not settings.bigmodel_api_key:
        raise RuntimeError("BIGMODEL_API_KEY 未配置，无法调用生成模型")
    client = OpenAI(base_url=settings.bigmodel_base_url, api_key=settings.bigmodel_api_key)
    try:
        response = client.chat.completions.create(
            model=settings.bigmodel_text_model,
            messages=[
                {"role": "user", "content": PROMPT.format(context=context, question=question)}
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content or "（模型返回为空）"
    finally:
        client.close()


def ask(question: str, embedder: BgeEmbedder, engine, batch_id: str) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        parents = retrieve(connection, embedder, batch_id, question)
    print(f"\n=== 引用证据（{len(parents)} 道父菜谱）===")
    for index, parent in enumerate(parents, 1):
        print(
            f"  {index}. {parent['name']} | section={parent['section']} "
            f"| cosine={float(parent['cosine']):.4f} | 来源={parent['source_asset_key']}"
        )
    print("\n=== 模型回答 ===")
    print(generate(question, build_context(parents)))


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文证据和回答需要 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query")
    parser.add_argument("--batch-id")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    model_dir = args.model_dir if args.model_dir.is_absolute() else ROOT / args.model_dir
    try:
        embedder = BgeEmbedder(model_dir)
        engine = make_engine(get_settings().database_url)
        with engine.connect() as connection:
            batch_id = args.batch_id or latest_ready_batch(connection)
        print(f"使用研发批次：{batch_id}")
        if args.query:
            ask(args.query, embedder, engine, batch_id)
        else:
            while True:
                question = input("\n问题（空行退出）> ").strip()
                if not question:
                    break
                ask(question, embedder, engine, batch_id)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"ask failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
