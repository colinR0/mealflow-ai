# 结构化知识库改为 typed SQL 精确查询

- 日期：2026-11-10 22:14
- 范围：`apps/api/app/db/v2_models.py`、`apps/api/app/services/structured_knowledge.py`、`apps/api/app/services/native_retrieval.py`、`apps/api/migrations/versions/l7d8e9f0a1b2_structured_knowledge_typed_sql.py`、`apps/api/tests/test_structured_knowledge_rag.py`、`scripts/import_structured_food_knowledge.py`
- 关联文档：`docs/README.md`

## 行为变化

- 食材替换、风味模板、禁忌/风险三张结构化表删除 `rag_text`、`embedding`、`embedding_profile_id`、`embedding_vector`、`search_tsv`，只保留类型化字段与审核元数据。
- `search_structured` 不再加载 embedding 模型或 provider，直接在 `approved` 行上做确定性 SQL 匹配，返回 `search_stage="typed_lookup"`。
- 支持食材别名、`applies_to` 与严重度排序；未知输入返回 `no_result`，并附带“这不代表饮食安全”的提示。
- 风险查询无命中时不再暗示安全；`check_food_risk` 仍作为工具入口保留。
- 结构化库不进入向量通道和 RRF；菜谱/技能向量检索保持不变。
- 导入脚本改为纯 SQL upsert，不再写向量列；保留 advisory lock 与幂等行为。

## 验证

- 在临时 PostgreSQL 上从空库执行 `alembic upgrade head`，成功到达 `l7d8e9f0a1b2`；迁移 head 为 `l7d8e9f0a1b2`，`down_revision = "k6f7a8b9c0d1"`。
- 设置 `MEALFLOW_TEST_DATABASE_URL`、`MEALFLOW_TEST_NATIVE_POSTGRES_URL` 后执行：
  `python -m pytest apps/api/tests/test_structured_knowledge_rag.py apps/api/tests/test_native_postgres.py apps/api/tests/test_recipe_rag_flow.py -q`
  结果：`26 passed, 26 warnings`。
- 修正 `test_native_postgres.py` 测试 schema 隔离：`Base.metadata.create_all(engine, checkfirst=False)`，避免 search_path 命中已迁移的 `public` 表后跳过临时 schema 建表，导致 HNSW 断言查不到索引。
- 本次改动文件的 `ruff check` 通过。

## 已知缺口

- 开发库 `127.0.0.1:5434/mealflow_s7` 的密码校验失败，迁移尚未应用；API 启动会被 schema guard 拦截，需在有权限的环境执行 `alembic upgrade head`。
- 网页菜谱回退、离线包和做饭记录属于后续阶段，尚未实现。