# MealFlow-AI 冰箱库存独立验收报告

日期：2026-11-07（Asia/Shanghai）
结论：本轮不能判定整体验收通过。前端检查真实通过；数据库、库存真实界面、Android 未独立验收。开发声称通过的结果没有被转述为本报告通过。

## 边界与环境

- 环境：colinBook Windows，`D:\Agent\mealflow-ai`，`dev`，Python 3.13.14、Node 22.22.3。
- 已读真实 `AGENTS.md`、开发记录、git diff。`AGENTS.md (M)`、`output/ (??)` 原样保留；未 reset/checkout/clean/提交/推送；未连接数据库或执行迁移；未改业务实现。只新增 qa-reports 内的报告、测试、截图和隔离浏览器运行数据。
- 新迁移实际在 `apps/api/migrations/versions/g2b3c4d5e6f7_inventory_ingredient_identity.py`。所谓 `apps/api/alembic/versions/g2b3c4d5e6f7_...py` 路径不存在，是转述错误。
- 在 `apps/api` 下运行 `..\..\ .venv/Scripts/python.exe -m alembic heads`（实际无空格）及迁移 history：退出码 0；`g2b3c4d5e6f7` 为唯一 head，前驱 `f1a2b3c4d5e6`。根目录直接指定 alembic.ini 报找不到 migrations，是工作目录错误，不是迁移测试失败。

## 已真实执行

在仓库根目录（另行标注除外）：

1. `git status --short --branch; git diff --stat; git diff --name-status`：退出码 0。
2. `Set-Location apps/api; ..\..\.venv\Scripts\python.exe -m alembic heads; ..\..\.venv\Scripts\python.exe -m alembic history -r f1a2b3c4d5e6:g2b3c4d5e6f7`：退出码 0，迁移链如前述。只读迁移图，未 upgrade。
3. `.venv\Scripts\python.exe -m ruff check apps/api`：退出码 1，293 errors（主要 E501，51 可自动修复）。这些是整仓结果，包括存量问题，不能全归本变更。
4. 对本次后端涉及文件运行 Ruff 含 models.py、seed.py：退出码 1，52 errors；忽略存量 E501 后 1 个 `UP017` 位于 `apps/api/app/db/models.py:28:25`，该行也是存量代码。本次新增文件未见 lint 错误。
5. `Set-Location apps/web; npm run typecheck`：退出码 0，vue-tsc 通过。
6. `Set-Location apps/web; node --test tests/inventory-retry.test.mjs`：退出码 0，9 pass、0 fail、0 skipped。该测试执行真实页面 methods，但 transport/uni 是 stub，不是完整端到端。
7. 以独立全新 Chrome 配置、390×844、`--headless=new` 打开 `http://127.0.0.1:5173/#/pages/fridge/inventory`：进程退出码 0，生成 `01-inventory-initial.png`。页面是手机号登录，不是库存页；未登录、未到达库存页面。此图不能作为库存 UI 通过证据。
8. `git diff --check -- [本次后端文件]`：实际退出码 0（见诊断记录首行 `DIFF_CHECK_EXIT=0`）。真实 diff 可见 models.py 第一行 BOM，但 diff-check 不认为其是 whitespace failure，不据此推断运行失败。

9. `.venv\Scripts\python.exe -m ruff check apps/api/app/api/routes/v2.py apps/api/app/db/v2_models.py apps/api/app/db/seed.py apps/api/app/seed_s7_demo.py apps/api/app/services/ingredient_catalog.py apps/api/app/services/inventory_lots.py apps/api/app/services/v2_runtime.py apps/api/migrations/versions/g2b3c4d5e6f7_inventory_ingredient_identity.py --output-format concise --ignore E501`：退出码 0，`All checks passed!`。只排除 models.py 存量告警及长行；不代表整个仓库 lint 通过。



## A-I 独立结论

| 范围 | 本轮状态 | 依据与缺口 |
|---|---|---|
| A 同标准名/别名/自定义同 ID 单记录、覆盖版本、审计 | 静态核实实现及已有用例；未真实运行 | 含稳定 id 与 upsert 代码，缺首次并发 upsert 验证。数据库不可执行。 |
| B 显示名变更不破坏关联、字典与 fallback | 静态核实；未真实运行 | ingredient_id 独立于 name；别名/分类/默认单位/自定义逻辑存在，未做数据库端到端。 |
| C 限定再次入库恢复，PATCH 不复活 | 静态核实；未真实运行 | upsert 设 active，PATCH 未修改 status；已有恢复生命周期用例。 |
| D 并发、409 重确认、失败审计回滚 | 前端 mock 测试部分通过；后端并发未验证 | Node 测试含 409 刷新重确认、失败刷新/替换版本；Python 有既有 lot 并发，但没有首次 upsert 并发。审计失败回滚未测。 |
| E 0、unknown/null、合法量、长度/单位 | 前端部分通过；其余未运行 | 原始十进制字符串测试通过；已有零、unknown、高精度用例未真正执行。 |
| F 搜索、筛选、分页、重试 | 部分通过 | 已知数量筛选、失败分页同 offset 及不清旧项测试通过；服务端标准名/别名端到端未验证。 |
| G 无不可用识别入口、空间权限 | 静态确认；运行未验证 | inventory.vue 已无识别按钮。首页仍保留“冰箱画面”，文字明示摄像头尚未接入，不属于真实可用识别。权限用例已读但未独立执行。 |
| H 审计/统计 | 未独立运行 | 实现、已有测试存在不等于通过。 |
| I Android 与减少动效 | 未验证 | 未找到 adb/emulator，未运行 Android build；只有 CSS prefers-reduced-motion，无设备实测。 |

## 一次修复需关注

### 1. 首次并发 upsert 风险（需求 D，尚未复现）

- **已查事实**：`apps/api/app/services/inventory_lots.py` `upsert_lot` 先 SELECT FOR UPDATE 尚不存在的行，再 INSERT；没有处理两个事务唯一约束失败的明确恢复/重试路径。
- **影响**：若数据库返回唯一冲突，可能给客户端 500 或事务不可用，重复双击可能失败。此后果需要 PostgreSQL 实际测试确认，不是已复现事实。
- **预期**：同 workspace + ingredient_id 同时第一次新增，最终只有一条记录、审计正确，失败方得到可处理结果，而不是未捕获错误。
- **复现**：在明确 disposable *_test PostgreSQL 用两个 session 经同一 barrier 同时 POST `/api/v2/households/{household_id}/inventory-lots`。在真实测试中比较两条 HTTP 结果、DB lot count、movements。不得借开发库；目前无明确测试连接，因此没跑。

### 2. 迁移无运行验证（升级风险，不是迁移已失败）

- 未见 upgrade/backfill/自定义 key/重复冲突/rollback 测试；本轮未执行。测试模板应在 *_test 复制老 schema 的代表数据，验证精确回填、自定义 fallback、非空、外键、索引、唯一约束，以及遇重复的失败原子性。不能对开发/演示库测试。

### 3. 真实 UI/Android 未覆盖

- 独立会话只有登录页。应由开发提供连隔离 *_test 后端的测试会话或本机无破坏性测试入口；未授权不碰真实账号。
- 没有 adb/emulator/HBuilderX 运行证据；本轮未独立构建 Android。需补实际设备/模拟器键盘、底部安全区、长列表、减少动效验收。

## 环境阻塞

- `MEALFLOW_TEST_DATABASE_URL` 在进程和 Shell 中未设置；仓库 `.env` 无该变量。README 只有示例，不足以确认专用 *_test 库与凭据。因为 conftest autouse fixture 会 `drop_all/create_all`，本轮未运行 pytest，绝不能拿开发/演示库“试一下”。IDE dataSources.xml 仅有 SQLite（项目要求 PostgreSQL），也未使用。
- H5/API 已在本机运行，未重启。实际浏览器展示登录守卫，未使用用户登录会话/凭据。


7. `Set-Location apps/web; node --test ..\..\qa-reports\inventory-20261107\test_inventory_ui_contract.test.mjs`：新增 3 个测试最终退出码 0，3 pass、0 fail、0 cancelled。覆盖重复确认的请求去重、三种数量筛选参数、409 后取消不再写入。真实执行 `inventory.vue` methods，网络与 modal 用 stub。先前测试自身的依赖/路径错误与未决 Promise 分别记录为 1 fail、3 fail、3 cancelled；这些是无效测试运行，不是业务失败，修正后才有 3 pass。

## 新增测试/证据
`test_inventory_ui_contract.test.mjs`：新增 Node 页面合同回归测试（真实导入页面脚本，stub 仅隔离网络/导航），命令和结果见本目录 Node 新增测试的 3 pass 记录。

`01-inventory-initial.png`：实际生成的新会话登录页，390×844；库存界面未验证。

`test_inventory_upsert_concurrency.py` 未创建：由于没有明确 *_test 环境，不留下从未运行的“通过”数据库测试；复现步骤已在前述条目写明。

本任务未改任何业务 `.py`、`.vue`、`.ts`；本轮完成一次开发修复交接后停止，不再开启另一轮。



- `D:\Agent\mealflow-ai\qa-reports\inventory-20261107\test_inventory_ui_contract.test.mjs`：本轮唯一新增并真实运行的测试文件。
- `D:\Agent\mealflow-ai\qa-reports\inventory-20261107\README.md`：独立报告。
- `D:\Agent\mealflow-ai\qa-reports\inventory-20261107\01-inventory-initial.png`：真实新会话登录页截图；不是库存 UI 通过证据。
- 没有新增 Python 测试，也没有数据库通过/失败数；原因见环境阻塞。已保留必要测试缺口供一次开发修复。






