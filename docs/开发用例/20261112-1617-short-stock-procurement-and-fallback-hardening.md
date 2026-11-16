# 用量不足并入采购卡与点名菜兜底加固

- 日期：2026-11-12 16:17
- 范围：`apps/api/app/agent/durable_graph.py`、`apps/api/app/services/recipe_fallback.py`、`apps/api/app/llm/durable_provider.py`、`apps/api/app/core/config.py`、`.env.example`、`apps/api/tests/test_durable_graph.py`、`apps/api/tests/test_recipe_fallback.py`、`apps/web/src/pages/assistant/index.vue`、`apps/web/tests/assistant-interactions.test.mjs`、`docs/README.md`
- 状态：已实现并通过自动化测试；未提交；未做 H5 手动点击和 Android 真机回归

## 问题与根因

- 上一版采购确认卡只收 `missing` 行：库存有但用量不足（`short`）的食材仍停留在“要加入采购吗？”的反问，用户需要多轮确认，而这类食材实际上同样做不成菜。
- 数量 `unknown` 的行没有可核对的采购量，上一版却在正文里反问“要加入采购候选吗？”，生成动作也缺少可信数量依据。
- 点名菜兜底 `lookup_recipe_tutorial()` 不接收图里的 provider，内部重新 `build_llm_provider(get_settings())`；worker 已在启动时构建 provider，二者配置来源不一致，且测试替身无法复用。
- 兜底抓取下厨房摘要不受任何开关约束，技巧检索的 `KNOWLEDGE_WEB_SEARCH_ENABLED` 管不到这条链路。
- 兜底解析把食材中文名直接写进 `ingredient_key`，而目录里并没有这个 key；将来“采购确认 → 入库”按 key 解析会失败。
- 每道未命中的点名菜都会串行走一次“网页抓取 + 最多两次模型调用”，多道菜时容易撞上 60 秒 run 预算。

## 本次决策

- `short` 进同一张采购确认卡，`quantity_status` 仍为 `unknown`、`quantity`/`unit` 仍为 `null`；卡片和 `note` 保留菜谱所需量与当前库存，不把库存量当成购买量。
- `unknown` 不进入采购草稿，正文改为陈述“请先在库存中核对数量”，不再反问一个无法给出数量的动作。
- 兜底复用注入的 provider；只有具备普通对话能力的 provider 才参与教程补写，工具型测试替身会被忽略并按配置重建。
- 点名菜联网兜底使用独立开关 `RECIPE_WEB_FALLBACK_ENABLED`，默认开启，与 `KNOWLEDGE_WEB_SEARCH_ENABLED` 解耦。
- 兜底食材不再伪造 `ingredient_key`，库存匹配和后续合并都走名称。
- 每轮最多兜底 2 道菜；剩余 run 时间不足 15 秒时直接跳过兜底，避免超时。

## 修改内容

- `_missing_procurement_action()` 重命名为 `_procurement_action()`，收集 `status in {"missing", "short"}`；按食材 key（缺失时用名称）聚合，新增 `available` 字段，`note` 写“菜谱所需：X，库存 Y”。
- `requested_dish_request()` 同步改为收集 `missing`/`short` 行；正文汇总改为“已把缺少的 X 和用量不足的 Y 整理成采购清单草稿”，`unknown` 单独提示且不生成动作。
- `Runtime.remaining_seconds()`：在租约栅栏内读取 run 创建时间，返回剩余预算秒数。
- `requested_dish_request()` 兜底调用传入 `provider=self.provider`；新增 `attempted` 标记、`MAX_RECIPE_FALLBACKS=2`、`MIN_FALLBACK_SECONDS=15`，超过预算直接按未找到处理。
- 兜底失败且确实尝试过时，用 `resolve_model_profile()` 区分“未配置模型”：新增 `no_model` 列表，回答明确写“本地菜谱库没有命中，且服务端未配置补充模型”，不再笼统提示稍后重试。
- `lookup_recipe_tutorial()` 新增 `provider`、`web_enabled` 参数；工具型 provider 自动忽略；联网前探测 `provider.ready()`，未配置模型时不发起任何网络请求。
- `_parse_recipe()` 把 `ingredient_key` 置为空字符串，并在注释中说明目录 key 未知、匹配必须用名称。
- `ConfiguredProvider` 新增 `chat()` 与 `ready()`，复用同一份模型配置并在 finally 中关闭 client。
- `Settings` 新增 `recipe_web_fallback_enabled: bool = True`；`.env.example` 增加 `RECIPE_WEB_FALLBACK_ENABLED=true` 及说明。
- 前端 `actionLines()` 在存在 `available` 时追加“库存 X”，卡片条目形如 `番茄 · 菜谱所需 1kg · 库存 0.5kg · 数量待核对`。

## 行为变化

- 点名菜谱比对库存后，缺少和用量不足的食材进入同一张批量采购确认卡；确认后仍只写 `pending` 采购项，不改库存。
- 用量不足项在卡片上同时显示菜谱所需量和当前库存，采购数量保持未知。
- 数量待核对的食材不会出现在卡片中，正文提示先核对库存。
- 未配置模型时，点名菜不再等待不必要的网页抓取，直接说明无法补充的原因。
- 单轮点名超过 2 道未命中菜时，第 3 道起不再联网/调模型，按“暂时没有可提供的做法”处理。
- 兜底食材按名称匹配 active 库存，不再依赖伪造的目录 key。

## 验证结果

- 后端：
  - `pytest apps/api/tests/test_recipe_fallback.py apps/api/tests/test_durable_graph.py apps/api/tests/test_v2_runtime.py -q`：**61 passed，7 skipped**。
  - 新增用例覆盖：`short` 进卡且带 `available`、`unknown` 不进卡、兜底每轮上限、未配置模型的显式降级、关闭联网开关、工具型 provider 忽略、无 provider 不触网。
  - `ruff check` 改动文件：通过；全仓仍有 295 个改动前已存在的告警，本次未处理。
- 前端：
  - `node --test tests/assistant-interactions.test.mjs`：**10/10 通过**。
  - `npm run typecheck`：通过。
  - `npm run build:app-android`：构建成功（仅有既有 Sass 弃用警告）。

## 已知限制

- 未启动 H5 手动走“点名菜 → 用量不足进卡 → 确认”，也未在 Android 真机验证新增的“库存”行；当前由后端图和前端组件测试覆盖。
- 测试环境强制清空 LLM key 并替换/跳过真实兜底，真实下厨房抓取与真实模型补写未在自动化中端到端验证。
- `available` 只是展示字符串，卡片采购数量仍是未知；没有把库存量换算成建议购买量。
- 兜底食材确认后 `ingredient_key` 落库为 `unknown`。当前没有“采购 → 入库”接口；将来实现时必须按名称解析，不能把 `unknown` 当目录 key。
- 每轮兜底上限是代码常量而非配置项；第 3 道及之后的未命中菜只能得到“暂时没有可提供的做法”。
- `remaining_seconds()` 每道未命中菜会额外开启一次带行锁的短事务。
- 网页抓取仍受全局节流锁约束，未改为并行，避免触发目标站点限流。
- 上一份记录 `20261112-1407-missing-ingredient-procurement-draft.md` 保持原样，本次未改写历史记录。