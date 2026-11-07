# 菜谱切分输入筛选与增量验收修复

日期：2026-11-07（Asia/Shanghai）

## 范围与文件

- 修改 `apps/api/app/cli/audit_recipe_loading.py`，新增切分候选与排除清单，并修复增量重跑丢失未变化父记录的问题。
- 修改 `apps/api/tests/test_audit_recipe_loading.py`，覆盖默认增量重跑与筛选结果数量守恒。
- 重新生成 `output/prechunk-audit-v1/` 下的验收报告；原始菜谱文件未删除或修改。

## 交付行为

- `ready_for_chunking.jsonl` 仅含已加载、结构完整、无错误、来源可用且无需食材解析复核的父菜谱。
- `excluded.jsonl` 保留每条排除记录的 ID、来源和排除原因；`parents.jsonl` 仍保留全量记录。
- 增量运行从上次父记录报告复用未变化来源，并将其问题一并计入新汇总；PDF 解析产物每次重读，样本清单变化时全量重处理。

## 验证与结果

- 定向测试：`pytest --noconftest apps/api/tests/test_audit_recipe_loading.py -q`，3 passed。
- 聚焦 lint：`ruff check apps/api/app/cli/audit_recipe_loading.py apps/api/tests/test_audit_recipe_loading.py`，通过。
- 真实数据全量及默认增量各运行一次，均为 979 条父记录、40 条切分候选、939 条排除记录；9 条多格式样本均保留。
- CLI 退出码 1 是全量报告包含原始数据错误项的既有行为；报告文件完整生成。

## 已知限制

- 40 条仅可作为下一步切分输入候选，不等于人工核验或生产发布许可；`publishable_count` 仍为 0。
- 旧版 937 条因使用权未核实被排除；一份 PDF 因食材解析疑点被排除。
- 当前加载器仍围绕已有 JSON、样本清单和外部候选目录组织，尚未实现任意 10,000 份来源的通用加载及容量验证。
