# RAG 全链路缺口修复：菜谱食材、离线检索、浏览 API 与索引运维

- 日期：2026-11-11 21:48
- 范围：`apps/api/app/db/models.py` 与迁移 `n9a0b1c2d3e4`；`apps/api/app/services/recipe_actions.py`、`food_constraints.py`、`offline_bundle.py`、`native_index_worker.py`；`apps/api/app/knowledge_worker.py`；`apps/api/app/api/routes/v2.py`；`apps/api/app/core/config.py`、`.env.example`；`scripts/import_howtocook_rag.py`、`scripts/rebuild_rag_index.py`、`scripts/cleanup_rag_generations.py`；`apps/web/src/pages/assistant/index.vue`、`apps/web/src/pages/shopping/recipes.vue`、`apps/web/src/shared/offlineSearch.ts`；对应后端/前端测试。
- 关联文档：`docs/README.md`

## 行为变化

- 菜谱食材增加文档回退：当 `recipe_ingredients` 尚未物化时，从已发布 `KnowledgeDocument.content["ingredients"]` 读取；导入脚本同步物化 `Ingredient` 与 `RecipeIngredient`，数量/单位缺失时保持 `NULL`，不推断 0、1 或“适量”。
- `recipe_ingredients` 增加 `(recipe_id, ingredient_id)` 唯一约束；迁移 `n9a0b1c2d3e4` 允许数量/单位为空并去重已有关系。
- 菜谱浏览与详情只返回 `published=true` 的文档数据；无食材数据的菜谱不再显示“忌口通过”，而是返回 `cannot_determine` 并明确“未执行/无法判断”。
- 网页菜谱回退默认关闭：`knowledge_web_search_enabled=False`，禁用时返回 `WEB_SEARCH_DISABLED`，不伪造网页来源或安全结论；显式启用时保留原有三态语义。
- 离线包新增 `search_tokens` 和 `offline_rules.search = "lexical_tokens"`。设备端按空白分词与 CJK bigram 做词法匹配，不调用 embedding 或模型；本地快捷操作会明确提示未执行服务端库存与忌口校验。
- 新增已发布菜谱只读接口：`GET /api/v2/households/{id}/recipes`（`query`、`limit`、`offset`）与 `GET /api/v2/households/{id}/recipes/{recipe_id}`；详情不存在返回 `404 RECIPE_NOT_FOUND`。
- 知识重建支持入队：`enqueue_rebuild()` 复用可运行的 queued/running/failed job；新增 `python -m app.knowledge_worker --once` 消费 `knowledge_rebuild`；`scripts/rebuild_rag_index.py --enqueue` 入队，`scripts/cleanup_rag_generations.py` 清理非活动 ready generation。
- 导入校验按当前 chunker 的实际 chunk 文本修正：每篇文档至少有一个完整 512 维 chunk，不再错误要求 `chunk.text == search_text`。

## 验证

- 迁移：开发库 `mealflow_rag` 从 `m8f9a0b1c2d3` 升到 `n9a0b1c2d3e4 (head)`。
- HowToCook 回填：443 道菜谱、1045 个食材、4026 条 `recipe_ingredients`；4026 条关系数量与单位均为 `NULL`，443 道菜谱均有食材。
- 导入校验：443 篇文档全部有完整 512 维索引；菜谱 source 的 443 篇文档对应 518 个 chunk（长菜谱允许拆块），活动 generation 共 613 个 chunk；`gateway=ok`、`agent_tool=ok`、native BGE。
- 重建队列：job `334730bb-3719-49ce-b0c2-4c7d292fd322` 成功消费，attempts=1，发布 generation `c07847fa-66c1-45b4-bba8-782fcc0af8d0`。
- 清理：`cleanup_rag_generations.py --keep 1 --apply` 保留 active `c07847fa...` 与回退 `2e200a38...`，删除 4 个旧 ready generation；旧 failed generation 记录保留用于审计。
- 离线包：443 道菜谱、19 项技能、819728 bytes、`data_version=bbbd2600b9ec020d`；菜谱 `search_tokens` 47 个，技能 `search_tokens` 256 个，`offline_rules.search=lexical_tokens`。
- 后端定向测试：`test_phase2_retrieval_templates.py`、`test_phase2_http.py`、`test_knowledge_worker.py` 共 `12 passed`。
- 既有烹饪技巧/RAG 回归：`test_cooking_skill_rag.py` `16 passed`。
- 全量后端：`298 passed, 16 skipped, 4 failed`。4 项为既有环境/断言问题（会话时间 `+08:00` 与期望 `+00:00`、默认模型/provider 期望），不经过本次 RAG 改动路径，未修改。
- 前端：`npm run typecheck` 通过；`npm run build:app-android` 完成，输出 `apps/web/dist/build/app`；Node 测试 3 个文件共 `18 passed`（含新增 `offline-search.test.mjs` 3 项）。
- 新增/修改文件聚焦 `ruff check` 通过；`apps/api/app/db/models.py` 全文件仍有既有 `UP017`/`E501`/`I001` 告警，本次新增约束行已格式化且无新增告警。`git diff --check` 无空白错误。

## 已知缺口

- Android 真机未验收；本次只完成类型检查、Android 构建、后端/API 测试和 Node 服务测试。
- 网页回退仍无稳定 provider/凭据，保持默认关闭；启用后仍需真实来源与内容质量验收。
- 全量后端 4 个失败项未在本次修复，需单独的时区/模型默认配置任务处理。
- `cleanup_rag_generations.py` 只清理非活动 `ready` generation；历史 `failed` 记录保留，若需要彻底清理由运维另行确认。