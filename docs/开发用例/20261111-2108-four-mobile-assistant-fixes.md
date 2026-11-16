# 移动端输入框、菜谱交接、助手超时与做饭记录返回修复

- 日期：2026-11-11 21:08
- 范围：`apps/web`、`apps/api`、`.env`/`.env.example`
- 状态：已实现并在 H5 验证；Android 仅完成构建，未上真机；改动未提交

## 问题与根因

1. **食材/库存输入框点不动**
   - H5 下 `uni-input` 包装层有高度，但内部原生 `input` 被压成 0px。
   - 手动新增库存的 `.form-section input`、库存编辑弹窗的 `.modal input` 只设置了 padding，没有显式高度；在 `box-sizing: border-box` 下内部输入区塌陷。
   - 做饭记录详情页的编辑标题输入框原生高度只有 3px，属于同一类样式问题。
   - 这不是功能未开发，而是 H5 组件样式兼容问题；做饭记录列表页的 `.input` 实测正常。

2. **菜谱“向助手咨询搭配”后聊天框空白**
   - `recipes.vue` 的 `askAssistant()` 只执行了 `uni.switchTab`，没有把菜谱内容传给助手页。
   - 助手页也没有读取待发送内容的入口，所以输入框始终为空。

3. **助手请求 `RUN_TIMEOUT`**
   - 失败请求卡在 `search_recipes`。本地 BGE 模型加载使用 `SentenceTransformer(..., local_files_only=False)`，会联网检查 HuggingFace；实测等待超过 100 秒。
   - 该检索在异步工具中同步执行，`asyncio.timeout` 无法打断它，最终 worker 在 60 秒判 `RUN_TIMEOUT`。
   - 模型与 LLM 配置本身正常，问题在检索模型的联网检查与同步阻塞。

4. **做饭记录无法返回**
   - 做饭记录列表/详情使用 `AppHeader`，该组件没有返回按钮。
   - 项目已有带返回逻辑的 `SubpageHeader`，但这两个页面没有接入。

## 修改内容

### 前端

- `apps/web/src/pages/fridge/manual-add.vue`
  - 食材名称输入框改为 `height: 44px; padding: 0 11px`。
  - 数量输入框改为 `height: 44px; padding: 0 9px`。
- `apps/web/src/pages/fridge/inventory.vue`
  - 编辑弹窗输入框改为 `height: 44px; padding: 0 12px`。
- `apps/web/src/pages/shopping/stock-in.vue`
  - 实际数量输入框改为 `height: 36px; padding: 0 8px`，避免同类塌陷。
- `apps/web/src/pages/cooking/index.vue`
  - 改用 `SubpageHeader`，恢复返回按钮。
  - `.input` 增加 `height: 88rpx; padding: 0 20rpx`。
- `apps/web/src/pages/cooking/detail.vue`
  - 改用 `SubpageHeader`，详情页关闭家庭切换（`:switchable="false"`）。
  - 编辑标题 `.input` 增加 `height: 88rpx; padding: 0 20rpx`。
- `apps/web/src/shared/assistantPrompt.ts`（新增）
  - 提供 `setAssistantPrompt` / `takeAssistantPrompt`，用本地存储传递待发送内容，读取后立即删除。
- `apps/web/src/pages/shopping/recipes.vue`
  - `askAssistant()` 根据选中菜谱生成问题（菜名、份量、时间、食材），写入待发送存储后切到助手页。
  - 未选中菜谱时写入通用“按库存推荐今天吃什么”的问题。
- `apps/web/src/pages/assistant/index.vue`
  - `onShow` 读取待发送内容，填充输入框并聚焦；按用户选择**不自动发送**。
  - 输入框增加 `:focus="composerFocused"` 与失焦复位。

### 后端与配置

- `.env`（本地运行配置，git 忽略）
  - 增加 `RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY=true`，禁止本地已缓存模型再联网检查。
- `.env.example`
  - 增加同名配置项（默认 `false`）及说明。
- `apps/api/app/agent/durable_graph.py`
  - `search_recipes`、`search_cooking_skills` 的同步检索改用 `asyncio.to_thread`，让工具超时能够生效。
  - `search_recipes` 工具超时设为 20 秒，避免首次加载模型被默认 5 秒超时打断。

## 行为变化

- 手动新增库存、库存编辑弹窗、做饭记录编辑标题均可正常点击输入；采购入库数量输入框按同类问题补了高度，未单独做入库页数据回归。
- 从菜谱详情点“向助手咨询搭配”会跳到助手页，输入框自动带入问题并聚焦；用户确认后自行发送。
- 做饭记录详情和列表左上角均有返回按钮；实际链路为“详情 → 做饭记录列表 → 我的”。
- 助手检索首次加载本地模型不再访问 HuggingFace；检索超时会返回工具超时结果，而不是把整轮请求拖到 60 秒 `RUN_TIMEOUT`。

## 验证结果

- H5 实测：
  - 手动新增食材输入框：原生 `input` 44px，包装层 44px。
  - 库存编辑弹窗：输入框原生 44px，包装层 44px。
  - 做饭记录详情编辑标题：修复前原生 3px；修复后恢复为 `88rpx` 设计高度（H5 宽屏按 rpx 换算更大，手机端约 44px）。
  - 菜谱交接：选中“番茄炒蛋”后，助手输入框自动填入完整问题，`document.activeElement` 为输入框，临时存储已清空。
  - 返回链路：从“我的”进入做饭记录，创建临时记录并进入详情，返回列表，再返回“我的”，均成功。
- 助手请求：
  - 发送“家里有 挂面鸡蛋 番茄 火腿 茄子，我想吃清汤面，一个人 告诉我怎么做”。
  - run `8dd16709-8063-4d64-886f-937b7520a728` 在约 24 秒内 `succeeded`；`search_recipes` 返回 `ok`，`duration_ms=16703`；回答正常显示。
- 自动化检查：
  - `npm run typecheck` 通过。
  - `node --test tests/inventory-retry.test.mjs`：9/9 通过。
  - `npm run build:app-android` 构建成功。
  - `pytest apps/api/tests/test_durable_graph.py -q`：14 passed，4 skipped。
  - `ruff check apps/api/app/agent/durable_graph.py` 通过。
- 清理：
  - 验证用临时库存、做饭记录已删除；`inventory_lots`、`inventory_movements`、`cooking_logs` 均为 0。
- 运行进程：
  - API PID `23540`，worker PID `24080`，API 监听 `127.0.0.1:8000`。
  - 日志位于 `C:\Users\Administrator\AppData\Local\Temp\opencode\mealflow-*-20261111-210547.*.log`。

## 已知限制

- 未在 Android 真机验证；当前验证为 H5 测量 + Android 构建通过。
- 采购入库页未单独做完整 H5 数据回归；其输入框修复与手动新增/库存编辑属于同一类样式修改。
- 首次检索在 worker 重启后约 16.7 秒，接近 20 秒工具超时；更慢的机器仍可能得到 `TOOL_TIMEOUT`，但不会再把整轮拖到 60 秒 `RUN_TIMEOUT`。
- `.env` 是本地文件，未提交；其他环境需要自行设置 `RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY=true`。
- 全量 `ruff check apps/api` 仍有仓库既有问题，本次只保证修改文件通过。
- 本次改动尚未提交。