# Phase 2：检索路由、服务端模板、网页回退、离线包与做饭记录

- 日期：2026-11-11 10:26
- 范围：`apps/api/app/services/food_constraints.py`、`recipe_actions.py`、`offline_bundle.py`、`web_recipe_fallback.py`、`photo_store.py`、`cooking_logs.py`；`apps/api/app/api/routes/v2.py`；`apps/api/app/agent/durable_graph.py`；`apps/api/app/db/v2_models.py` 与迁移 `m8f9a0b1c2d3`；`apps/api/tests/`；`apps/web/src/pages/assistant/index.vue`、`pages/cooking/*`、`pages/family/index.vue`、`pages.json`。
- 关联文档：`docs/README.md`

## 行为变化

- 检索按数据类型分流：成员、家庭状态、采购走 SQL；菜谱/技能走关键词 + 向量 + 文档级 RRF；食材替换、风味配比、禁忌风险改为 `approved` 行上的 typed SQL，不再进入向量通道。`check_food_risk` 未命中返回 `no_result`，不输出“安全”。
- 新增服务端确定性忌口校验与固定模板：`忌口校验：已对照：{成员·类型·食材}；结论：通过/不可推荐`。命中过敏/忌口时更换菜谱，不通过替换食材规避。未选出可校验菜谱时保持 `忌口校验：未执行：未选出可校验的菜谱；结论：无法判断`。
- 新增 `POST /api/v2/households/{id}/assistant/quick-actions`，支持 `inventory`、`restrictions`、`skills` 与 `offline`；模板、数据版本和校验行均由服务端生成，模型只能补充简短理由。
- 新增网页菜谱回退 `web_recipe_fallback.py` 并接入 `check_web_recipe` 工具：由模型提取来源与食材，服务端匹配家庭忌口。状态固定为 `matched`、`not_detected_unverified`、`cannot_determine`；未检出时明确“未经全文核实，不能视为安全”，不写“通过”。
- 新增 `GET /api/v2/households/{id}/offline-bundle`：从 `recipes`、已验证技能和家庭限制导出 schema v1 包，附 `data_version` 与 `ETag`；声明 `lexical_only`、不生成、写入需幂等键和版本、冲突 409 不自动合并。
- 做饭记录独立于聊天：`cooking_logs`、`cooking_log_dishes`、`cooking_log_photos`、`cooking_log_comments`。全家可查看和评论，仅创建者可编辑/删除照片；评论不增加 `version`，创建者编辑和照片操作增加版本。PATCH 与照片删除携带 `expected_version`，冲突返回 409。
- 照片以原始 JPEG/PNG 请求体上传，8 MB 上限，写入前移除 JPEG APP1 与 PNG eXIf/tEXt/iTXt/zTXt 元数据；当前使用本地文件系统适配器，读取需登录且返回私有缓存头。
- 助手仅在记录创建者手动开启 `allow_assistant_citation` 后加载 `log:<id>` 证据；默认关闭的做饭记录无法被引用，非法引用仍返回 `INVALID_CITATION`。
- 移动端助手页新增【按库存推荐】【查忌口食谱】【检索烹饪技巧】快捷操作、离线开关与“保存离线包”；新增“我的”页入口及“我的做饭记录”列表/详情页，支持创建、只看我做的、评论、创建者编辑和引用授权开关。

## 验证

- Phase 1 typed SQL 回归：`26 passed`。
- `test_phase2_retrieval_templates.py`：`7 passed`；覆盖固定校验行、未命中不称安全、离线包按名称检索和 `data_version`。
- `test_cooking_logs.py`：`2 passed`；创建、创建者编辑、评论、`mine` 与引用 opt-in。
- `test_cooking_log_photos.py`：`2 passed`；JPEG/PNG 元数据剥离、创建者限制、删除文件。
- `test_cooking_log_http.py`：`2 passed`；登录、401、403、409、评论、照片上传/读取。
- `test_phase2_web_fallback.py`：`3 passed`；`matched`/`not_detected_unverified`/`cannot_determine` 与禁用“安全/通过”措辞。
- `test_durable_graph.py::test_cooking_logs_are_citable_only_after_explicit_opt_in`：`1 passed`。
- `test_durable_graph.py::test_web_recipe_fallback_matches_household_restriction`：`1 passed`；`check_web_recipe` 命中过敏时不可推荐，未检出时不称安全。
- `test_durable_graph.py` 全文件：`14 passed, 4 skipped`；`test_protocol_guard.py` 与 `test_cooking_retrieval_prompt.py` 同批通过。
- `test_phase2_http.py`：`1 passed`；快捷操作/离线包 401、`ETag` 与 `Cache-Control`。
- 变更后端文件 `ruff check` 通过，`durable_graph.py` 编译通过。
- 前端 `npm run typecheck` 通过；`npm run build:app-android` 构建完成，输出 `apps/web/dist/build/app`（非签名 APK）。

## 已知缺口

- 开发库 `127.0.0.1:5434/mealflow_s7` 不可用，迁移只在一次性测试库验证；上线前需在目标库执行 `alembic upgrade head`。
- 移动端离线包目前仅下载并保存在本机，尚未接入真正的设备端词法检索；离线按钮仍通过服务端 `offline=true` 渲染模板。
- 移动端尚未提供做饭记录照片上传/删除 UI；详情页可展示已上传照片。
- 生产对象存储未配置，照片当前落在 `data/cooking_photos` 本地目录；多实例部署需替换存储适配器。
- 真实机型上的快捷操作、引用开关和照片鉴权展示仍需设备验收。