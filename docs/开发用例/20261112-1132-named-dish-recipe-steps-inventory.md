# 点名菜先给食材和做法，再核对库存并询问采购

- 日期：2026-11-12
- 用户确认：本次需要填写开发记录。
- 范围：助手页点名菜请求。先检索本地 RAG；没有可用菜谱时尝试网络教程和已配置 LLM；服务端继续执行忌口校验、有效库存核对和采购确认。点名菜回复不再展示过程提示。

## 问题与行为

1. 原点名菜流程只返回菜名、库存缺口和采购询问，没有展开食材和做法；用户追问“菜谱呢”时仍可能只得到缺口信息。
2. 现在优先使用 RAG 命中的菜谱，展开食材和编号步骤；RAG 没有可用候选（例如只有食材没有步骤）或检索不可用时，调用新增的菜谱教程兜底服务。
3. 兜底服务先尝试下厨房实时搜索摘要，再用已配置的 LLM 整理为严格 JSON 食材/步骤。模型输出不能直接视为已核实：
   - 网络摘要存在时显示“做法来源：网络教程（真实 URL）· 未核实”；
   - 只有模型补充时显示“做法来源：模型补充 · 未核实”；
   - 不构造网页链接，不伪造来源。
4. 兜底内容仍由服务端 `check_recipe` 和 `recipe_conflicts` 复核；命中过敏/忌口时整道菜不推荐。食材名中的已知说明性脏数据（如“如果附近菜市场没有”“请参考制作[焖面]）”“需带笼屉”）继续用 `_NON_FOOD_NAME` 过滤，避免进入展示、忌口判断和库存分析。
5. 回复顺序统一为：菜名/来源 → 忌口校验 → 食材 → 编号步骤 → 当前有效库存缺口/未知数量 → 是否加入采购清单。未确认前不写入采购清单。
6. 只读取 `active` 库存；未知数量保持未知，不推断为 0。兜底食材的用量只作文本展示，`capabilities.quantities=false`，不参与库存数量计算。
7. 点名菜响应清空 `warnings`，并通过 `hide_thinking` 让 `thinking_steps` 为空；不再显示“已先检索菜谱”等过程提示。RAG 命中时保留真实 recipe 引用。

## 文件

- `apps/api/app/agent/durable_graph.py`
- `apps/api/app/services/recipe_fallback.py`（新增）
- `apps/api/tests/test_durable_graph.py`
- `apps/api/tests/test_recipe_fallback.py`（新增）
- `docs/开发用例/20261112-1132-named-dish-recipe-steps-inventory.md`

## 验证结果

- 静态检查：Ruff、`py_compile` 通过。
- 目标测试：`38 passed, 4 skipped`（`test_durable_graph.py`、`test_recipe_fallback.py`，使用 disposable `mealflow_rag_test`）。
- 扩展回归：`test_recipe_rag_flow.py`、`test_chat_api.py` 为 `22 passed, 4 skipped, 1 failed`。失败项是 `test_list_sessions_returns_recent`：测试断言 `updated_at` 以 `+00:00` 结尾，本机返回 `+08:00`；与本功能改动无关，时间/时区环境导致。
- H5 实测“今天中午想吃卤面”：命中《蒸卤面》，返回干净食材、12 步做法、缺少“猪五花肉、芹菜、鲜面条、青椒”和采购询问；`warnings=[]`、`thinking_steps=[]`、页面无思考过程块。
- H5 实测“今天中午想吃浆水面”：RAG 未命中，模型兜底返回 5 步做法，显示“做法来源：模型补充 · 未核实”，缺少“面条、浆水、五花肉”，用量文案为“按 2 人份参考上述食材用量；采购时按实际需要调整”；`warnings=[]`、`thinking_steps=[]`、`citations=[]`、`evidence=[]`。

## 已知边界

- 下厨房实时搜索在本次“卤面”“浆水面”测试中未返回可用结果，实际兜底由 LLM 完成；模型内容未经外部来源核实，界面已标注。
- RAG 食材数组中的脏数据目前靠 `_NON_FOOD_NAME` 的已知模式过滤；根因在菜谱导入清洗，后续应继续完善 ingest 侧的数据规范化。
- 兜底食材用量是模型给出的文本参考，不用于库存数量计算；采购写入仍需用户明确确认。
- 本次未提交、未推送，工作区已有的其他修改保持原样。