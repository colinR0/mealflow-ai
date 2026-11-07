# 冰箱库存改版：稳定食材身份与 upsert

日期：2026-11-07 00:13（Asia/Shanghai）

## 范围

- 移动端 v2 inventory-lots UI 重设计：搜索/状态/数量筛选、分页、详情、编辑、用完、软移除、空态手动新增。
- 移除库存入口不可用的识别录入按钮；家庭在库存相关页静态展示（不可切换）。
- 预置食材别名与稳定 `ingredient_id`；同家庭同食材唯一逻辑库存；新增改为覆盖式 upsert。
- 终态（已用完/已移除）仅能通过入库 upsert 恢复 active；普通 PATCH 不复活。
- 提供 Alembic 迁移 `g2b3c4d5e6f7`（未对开发/演示库执行）。
- 不做批次、保质期扩展、自动扣减、采购联动、识别/YOLO/相机实现。

## 实际修改文件

- 后端模型：`apps/api/app/db/models.py`、`apps/api/app/db/v2_models.py`
- 服务：`apps/api/app/services/ingredient_catalog.py`（新）、`apps/api/app/services/inventory_lots.py`（新）、`apps/api/app/services/v2_runtime.py`
- API：`apps/api/app/api/routes/v2.py`
- 种子/演示：`apps/api/app/db/seed.py`、`apps/api/app/seed_s7_demo.py`
- 迁移：`apps/api/migrations/versions/g2b3c4d5e6f7_inventory_ingredient_identity.py`（新，未应用）
- 测试：`apps/api/tests/test_inventory_lifecycle_api.py`
- 前端：`apps/web/src/pages/fridge/inventory.vue`、`apps/web/src/pages/fridge/manual-add.vue`、`apps/web/src/pages/fridge/index.vue`（空态去识别入口）、`apps/web/src/components/SubpageHeader.vue`、`apps/web/src/shared/types.ts`、`apps/web/src/shared/transport.ts`、`apps/web/tests/inventory-retry.test.mjs`

未改动：`AGENTS.md`、`output/`。

## 行为摘要

- `POST /inventory-lots` 按 `ingredient_id` / 校验后的 `ingredient_key` / 标准名与别名精确匹配解析食材；未知名称创建稳定自定义食材并复用。
- 同一 `workspace_id + ingredient_id` 有则覆盖 quantity（非累加），返回 `outcome=created|updated|restored`。
- `GET /api/v2/ingredients` 支持字典搜索（含别名）。
- 迁移仅按 `ingredient_key == ingredients.key` 精确回填；无法映射则保留为自定义食材；若出现重复 workspace+ingredient 则迁移失败并列出 lot id。

## 验证

- 解释器：仓库 `.venv`（`D:\Agent\mealflow-ai\.venv\Scripts\python.exe`）。AGENTS 指定的 `D:\APP\miniconda3\envs\mealflow\python.exe` 在本机不存在。
- `ruff check`（本次改动相关文件）：通过。
- `pytest apps/api/tests/test_inventory_lifecycle_api.py`：28 passed（`mealflow_s7_test`）。
- `pytest apps/api/tests/test_inventory_api.py`：6 passed。
- `node --test tests/inventory-retry.test.mjs`：9 passed。
- `npm run typecheck`：通过。
- `npm run build:app-android`：Build complete（Sass legacy 警告仍在）。
- 未执行 Alembic upgrade；未触碰开发库 `mealflow_s7`。
- 未做 Android 真机/模拟器 UI 验收。

## 缺口

- 迁移需在确认目标库后由人工执行；冲突数据需人工清理后再升级。
- 真机键盘/底部安全区与长列表滚动体验待独立验收。
- 冰箱首页仍保留“冰箱画面”入口文案（未实现相机）；库存页已去掉识别录入。


## 2026-11-07 修复轮：首次并发 upsert

### 问题

独立验收报告 `qa-reports/inventory-20261107/README.md`：`upsert_lot` 在行不存在时 `SELECT FOR UPDATE` 锁不住空缺行，两事务可并插，后败方可能唯一冲突/500 并使事务不可用。

### 修复

- 文件：`apps/api/app/services/inventory_lots.py`
- 算法：事务级 `pg_advisory_xact_lock(hashtext(workspace_id), hashtext(ingredient_id))`，仅串行化同一家庭+食材；锁后再 `SELECT FOR UPDATE`，无行则 nested savepoint 插入；若仍撞 `uq_inventory_lots_workspace_ingredient`，savepoint 回滚后重新锁读并走覆盖/终态恢复分支，外层事务保持可用。
- 边界：不锁跨 workspace / 不同 ingredient；普通 PATCH 仍不复活；数量仍覆盖非累加；旧迁移未改。
- 测试：`apps/api/tests/test_inventory_lifecycle_api.py` 新增 `test_concurrent_first_upsert_creates_single_lot`（Barrier + 双 HTTP POST）。

### 验证（本轮）

- 解释器：仓库 `.venv`（`D:\Agent\mealflow-ai\.venv\Scripts\python.exe`）；AGENTS 指定 Conda `mealflow` 仍不存在。
- `MEALFLOW_TEST_DATABASE_URL=postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7_test?gssencmode=disable`（README 示例；`current_database()`=`mealflow_s7_test`）；未触碰开发库 `mealflow_s7`，未执行 Alembic upgrade。
- `python -m ruff check apps/api/app/services/inventory_lots.py apps/api/tests/test_inventory_lifecycle_api.py --ignore E501`：通过。
- `pytest apps/api/tests/test_inventory_lifecycle_api.py::test_concurrent_first_upsert_creates_single_lot` 及既有并发/覆盖/恢复用例：6 passed。
- `pytest apps/api/tests/test_inventory_lifecycle_api.py`：29 passed（含新增 1 项）。
- 本轮未改前端；未跑 typecheck / Android / Node UI。
- 迁移实际路径确认：`apps/api/migrations/versions/g2b3c4d5e6f7_inventory_ingredient_identity.py`（非 alembic/versions）。

### 仍未验证 / blocked

- 迁移 upgrade / 回填冲突场景（未对任何库执行 upgrade）
- 真实 UI / Android / 键盘与底部安全区
- 本轮按约定停止，不开启额外测试/修复轮次
