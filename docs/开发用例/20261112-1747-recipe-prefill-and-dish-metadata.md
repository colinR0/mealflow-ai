# 菜谱预填收敛与菜名元数据剥离

- 日期：2026-11-12 17:47
- 范围：`apps/web/src/pages/shopping/recipes.vue`、`apps/web/tests/assistant-interactions.test.mjs`、`apps/api/app/agent/durable_graph.py`、`apps/api/tests/test_durable_graph.py`、`docs/README.md`
- 状态：已实现并通过自动化测试与 Android CLI 构建；未提交；未做 H5 手动点击和真机回归

## 问题与根因

### 1. 咨询搭配时预填了食材清单

- 现象：菜谱详情点击“向助手咨询搭配”后，助手输入框出现“我想做凉皮（2 人份，约 30 分钟）。菜谱食材：凉皮、面筋、盐……”整段内容。
- 根因：`recipes.vue` 的 `askAssistant()` 主动拼接了 `servings`、`duration_minutes` 和 `activeIngredients/ingredient_names`，预填内容不是单纯的做菜请求。
- 影响：用户还要手动删除大量食材文字，且人数被固定成菜谱默认值，无法按实际用餐人数提问。

### 2. “2人份/约30分钟”被当成菜名

- 现象：回答出现“［约 30 分钟］ 对应《约30分钟家常菜》”，以及“「凉皮（2人份」对应《凉皮》”。
- 根因：`requested_dish_names()` 取“做”之后的整段文本，再按 `，` 切分。`凉皮（2人份，约30分钟）` 被切成：
  - `凉皮（2人份`
  - `约30分钟）`
- 第一段靠“凉皮”子串仍能匹配到《凉皮》，所以显示成“凉皮（2人份”；第二段没有本地菜谱，被交给兜底模型，模型据此编出《约30分钟家常菜》。
- 叠加原因：`_DISH_QUANTITY` 只认“2份/2个/2碗”，不认“2人份”，即使写成“凉皮，2人份”也会多出一个“2人份”菜名。

## 本次决策

- 菜谱详情只预填菜名和标准请求，不预填人数、时长、食材清单；用户在发送前自行补充“2人份/3个人”等人数信息。
- 菜名抽取先去掉括号类元数据，再按标点切分；纯人数/时长 token 直接丢弃，包括中文数字写法。
- 没有选中菜谱时仍保留原来的通用推荐提示词。

## 修改内容

- `apps/web/src/pages/shopping/recipes.vue`
  - `askAssistant()` 选中菜谱时固定为：
    `我想做{菜名}。请结合我家现有库存，告诉我是否够用，还缺什么，并给我具体做法。`
  - 移除人数、时长和 `activeIngredients`/`ingredient_names` 的拼接。
- `apps/api/app/agent/durable_graph.py`
  - 新增 `_DISH_BRACKETED`：剥离 `（…）`、`(…)`、`【…】`、`［…］` 成对元数据。
  - 新增 `_DISH_BRACKET_TAIL`：处理未闭合括号，避免残留内容进入菜名。
  - 新增 `_DISH_SERVING_SUFFIX`：剥离 token 末尾的“约30分钟/2人份/三人/2位”等人数与时长，支持阿拉伯数字和中文数字。
  - `requested_dish_names()` 在切分前先清括号，在 token 清洗阶段再清人数/时长后缀。
- 测试
  - 后端新增 `test_requested_dish_names_strip_servings_and_time_metadata`，覆盖标准预填、全角圆括号、全角方括号、空格分隔、逗号分隔和中文数字时长。
  - 前端扩展 `loadPage()` 支持注入 `uni` 与模块，新增菜谱详情预填断言，验证不含食材、人数和时长。

## 行为变化

- 点击“向助手咨询搭配”后输入框只出现：
  `我想做凉皮。请结合我家现有库存，告诉我是否够用，还缺什么，并给我具体做法。`
- 用户补充“2人份”后，`explicit_constraints()` 的既有规则会解析为 2 人，库存核对与教程按 2 人份展开。
- `我想做凉皮（2人份，约30分钟）`、`我想做凉皮［2人份，约30分钟］`、`我想做凉皮 2人份`、`我想做凉皮，2人份` 都只抽取“凉皮”。
- 不再生成《约30分钟家常菜》，也不再出现“凉皮（2人份”这种残缺菜名。
- 未选中菜谱时的通用推荐提示词保持不变。

## 验证结果

- 后端：
  - `pytest apps/api/tests/test_durable_graph.py -q`：**51 passed，7 skipped**。
  - `test_durable_graph.py`、`test_recipe_fallback.py`、`test_v2_runtime.py`、`test_recipe_rag_flow.py` 回归：**68 passed，11 skipped**。
  - `ruff check` 改动文件：通过。
- 前端：
  - `node --test tests/assistant-interactions.test.mjs`：**11 passed，0 failed**。
  - `npm run typecheck`：通过。
  - `npm run build:app-android`：构建完成，输出 `apps/web/dist/build/app`。

## 已知限制

- 菜名中的括号如果本身是菜名的一部分（极少见），也会被当作元数据剥离；当前没有发现这类已收录菜名。
- 人数/时长只过滤“数字/中文数字 + 人份/人/位/分钟/小时”的常见写法；“半小时”“一个半小时”等口语仍未覆盖。
- 截图里兜底教程食材出现“盆”等厨具词，属于联网摘要/模型生成的食材质量问题，本次未处理；与菜名切分不是同一处逻辑。
- 未做 H5 手动点击和 Android 真机回归。