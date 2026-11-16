# 查忌口食谱未命中时自动走 LLM 补充

- 日期：2026-11-12
- 用户确认：本次需要填写开发记录。
- 范围：助手页「查忌口食谱」快捷检索在本地没有匹配的已发布菜谱时，自动使用已配置的 LLM 补充菜名、食材和步骤，并由服务端重新执行忌口校验；不改变库存写入、采购确认和离线模式。

## 问题与行为

1. 原快捷检索只查本地已发布菜谱。未命中时直接返回「菜谱库中没有匹配的已发布菜谱」，虽然响应带有 `fallback_allowed=True`，但前端和后端都没有继续调用模型或实时检索。
2. 现在仅在以下条件同时满足时自动补充：动作是 `restrictions`、非离线模式、本地没有候选菜谱（`fallback_allowed=True`）。`inventory`、`skills` 和离线模式仍走原有确定性路径。
3. 补充使用本地 `.env` 中已经配置的 LLM provider/model/key；开发记录和接口响应不包含密钥。模型被要求只返回严格 JSON：菜名、带大致用量的食材列表、步骤列表。格式无法解析时重试一次。
4. 模型生成的食材不会直接被视为安全。服务端把食材交给现有 `check_recipe` 重新匹配家庭成员过敏和忌口：
   - 命中过敏时明确「不可推荐」；
   - 命中忌口时提示「建议更换菜谱」；
   - 未命中或没有登记限制时也写明「模型做法未核实，不能视为安全」。
5. 返回模板为 `web`，正文写明「本地菜谱库未收录」「已用模型补充」「未经来源核实」；前端标签显示「模型补充 · 未核实」。不构造网页 URL，不声称是已核实的实时菜谱来源。
6. 模型不可用或返回内容不完整时，返回可重试的提示，不把模型故障伪装成「菜谱库找不到」，也不返回 500。

## 文件

- `apps/api/app/services/recipe_actions.py`
- `apps/api/app/api/routes/v2.py`
- `apps/api/tests/test_phase2_retrieval_templates.py`
- `apps/api/tests/test_phase2_http.py`
- `apps/web/src/pages/assistant/index.vue`
- `docs/开发用例/20261112-1052-llm-recipe-fallback.md`

## 验证结果

- `ruff check`：通过。
- `py_compile`：通过。
- 后端目标测试：`test_durable_graph.py`、`test_phase2_retrieval_templates.py`、`test_phase2_http.py`，结果 `52 passed, 4 skipped`。使用 disposable 测试库 `mealflow_rag_test`，未写入开发库。
- 前端 `npm run typecheck`：通过。
- 前端 `node --test tests/assistant-interactions.test.mjs`：`9 passed`。
- 真实 H5 验证：输入「虾仁蒸蛋」点击「查忌口食谱」，约 12 秒后返回模型补充结果，包含食材、5 步做法、`模板=web`、`fallback_allowed=false`；卡片显示「模型补充 · 未核实」，不再出现「菜谱库中没有匹配」。
- 真实 H5 忌口校验：当前家庭登记了「我·忌口·虾仁」，返回的校验行为「已对照：我·忌口·虾仁；模型做法未核实，建议更换菜谱」，命中项结构完整。未登记过敏场景由单元测试覆盖，命中花生过敏时明确返回「不可推荐」。

## 已知边界

- 下厨房实时搜索当前返回空，DuckDuckGo 返回 `WEB_SEARCH_FAILED`；因此本次实际兜底是 LLM 通用做法，不是实时网页全文。结果已明确标注「未核实」，没有伪造来源链接。
- 忌口结论只依据模型列出的食材；模型漏列或用户实际使用不同配料时，服务端无法发现。页面已提示以实际配料为准。
- 本次只覆盖「查忌口食谱」快捷检索。普通聊天中的点名菜仍走既有 RAG、active 库存和采购确认流程。
- 未改动库存、采购、成员偏好等写入路径；本次没有新增数据库迁移。