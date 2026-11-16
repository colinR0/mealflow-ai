# 缺失食材直接生成采购清单确认卡

- 日期：2026-11-12 14:07
- 范围：`apps/api/app/agent/durable_graph.py`、`apps/api/app/services/v2_runtime.py`、`apps/web/src/pages/assistant/index.vue`、相关后端/前端测试
- 状态：已实现并通过自动化测试；未提交；未做 H5 手动点击和 Android 真机回归

## 问题与根因

- 点名菜谱的确定性流程在比对 active 库存后，如果缺少主料，只返回“要我把缺少的……加入采购清单吗？”并进入 `waiting_input`，用户还要再回复一次才能生成确认动作。
- 现有 `add_procurement` 只支持单个 `payload.name`，无法把多道菜的多项缺失食材合并成一张确认卡。
- 确定性回答直接返回 `response`，而 `Runtime.validate()` 原先只从 tool message 的 `data.action` 提取动作，因此即使流程生成动作也到不了 worker。
- 前端确认卡也只按单个食材渲染，无法展示批量条目和菜谱所需量。

## 本次决策

- 用户明确选择：确认后只写采购清单（`shopping_items`，状态 `pending`），不写实际库存 `inventory_lots`。
- 所有采购数量统一为“数量未知”；确认卡展示菜谱所需量，避免把菜谱用量误当成实际购买量。
- 保持既有单条 `add_procurement` 行为，兼容“买点鸡蛋”等自然语言采购流程。

## 修改内容

- `_inventory_analysis()` 为每行补充 `ingredient_key` 和 `required_display`。`required_display` 优先使用可靠换算量，否则使用菜谱原始 `amount`，仅用于展示。
- 新增 `_missing_procurement_action()`：按食材聚合缺失行，生成一张 `add_procurement` 动作，payload 使用 `items[]`，每项包含 `name`、`ingredient_key`、`quantity_status: unknown`、`quantity/unit: null`、`required` 与 `note`。
- `requested_dish_request()` 不再询问“要我把缺少的……加入采购清单吗？”，改为直接返回 `waiting_confirmation`，并在响应中附带批量动作；文本改为“已整理成采购清单草稿，请核对后确认”。
- `Runtime.validate()` 在响应携带 `action` 时把动作写入最终结果，worker 可据此创建 `PendingAction`。
- `validate_action()` 支持 `add_procurement` 的 `items[]`：校验非空、最多 50 项、每项名称和数量状态；保留旧版单条 payload。
- `decide_action()` 在同一事务中为批量动作创建多条 `ShoppingItem`，返回 `shopping_item_ids`；单条时继续保留 `shopping_item_id`。
- 助手确认卡新增 `actionTitle()`、批量 `actionLines()` 和 `actionNote()`：显示“加入采购清单（N 项）”、每项菜谱所需量和“数量待核对”，并注明确认后不写入库存。

## 行为变化

- 点名菜谱缺少食材时，直接出现一张采购清单确认卡，不再多问一轮。
- 卡片中每项食材显示菜谱所需量（若菜谱提供）；采购数量仍为未知，不推断实际购买量。
- 用户确认后只新增待购买采购项，库存不变；用户拒绝则不写任何业务数据。
- 没有缺失食材、但存在用量不足或库存数量待核对时，仍沿用原有提示逻辑。
- 原有单条采购确认流程保持兼容。

## 验证结果

- 后端：
  - `ruff check` 通过。
  - 新增 worker 端到端用例：点名“卤面” → 一张批量待确认卡 → 确认后写入 2 条 `pending` 采购项，`inventory_lots` 仍为 0。
  - `pytest apps/api/tests/test_durable_graph.py apps/api/tests/test_v2_runtime.py -q`：52 passed，7 skipped。
- 前端：
  - `node --test tests/assistant-interactions.test.mjs`：10/10 通过。
  - `npm run typecheck` 通过。
  - `npm run build:app-android` 构建成功（仅有既有 Sass 弃用警告）。
- `git diff --check`：无空白错误，仅有 Git 的 LF/CRLF 提示。

## 已知限制

- 未启动 H5 页面手动走完整“点名菜谱 → 确认卡 → 确认”聊天流程；当前由后端图和 worker 测试、前端组件测试覆盖。
- 未在 Android 真机验证新增确认卡的多行布局。
- 本次只把 `missing` 食材合并为动作；`short` 和数量 `unknown` 的库存仍保留原有提示，未自动生成采购草稿。
- 同一食材出现在多道菜时，卡片把多个菜谱所需量用“、”合并展示，不逐道拆分来源。