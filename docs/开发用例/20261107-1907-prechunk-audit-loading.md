# 菜谱切分前自动验收加载实现

日期：2026-11-07 19:07（Asia/Shanghai）

## 范围与文件

- 新增 `apps/api/app/cli/audit_recipe_loading.py`：只读切分前验收 CLI（`python -m app.cli.audit_recipe_loading`）。
- 新增 `apps/api/tests/test_audit_recipe_loading.py`：路径/摘要/解析失败与幂等文件模式定向测试。
- 新增运行报告目录 `output/prechunk-audit-v1/`（`summary.json` / `parents.jsonl` / `issues.jsonl` / `manual_review.csv`）。
- 未修改 `AGENTS.md`；未执行开发/演示库迁移；未提交推送；保留库存相关改动与既有 `output/`、`qa-reports/`。

## 交付行为

- 处理顺序：读取来源登记 JSON → 校验存在性/格式/SHA-256 → JSON 直接评估、MD/TXT 解析、PDF 使用 MinerU 产物 → 统一父记录 → 自动质量验收 → 原子写出报告。
- 稳定身份：样本 `rload-001`～`rload-009` 使用清单 ID；JSON 不以数组下标/菜名/单独 `bv` 作长期主键，建立来源文件 + 源记录线索 → UUID 映射并报告 `IDENTITY_*`。
- 状态区分 `discovered/parsed/loaded/needs_review/rejected`；`publishable` 默认不自动授予；可切分（`chunkable`）与可发布分离。
- 复用 `scripts/evaluate_recipes.py` 问题代码语义（缺步骤、未知用量、权利未核实等）；第一版拒绝 `--commit-db`。
- 支持 `--registry-report`、`--input`、`--output-dir`、`--dry-run`、`--rule-version`、`--parser-version`、`--seed`、`--force`；相对路径相对仓库根解析。

## 验证结果

- 解释器：AGENTS 指定 Conda `D:\APP\miniconda3\envs\mealflow\python.exe` 不存在；实际使用仓库 `.venv\Scripts\python.exe`。
- 定向测试（无需 PostgreSQL，`pytest --noconftest`）：`tests/test_audit_recipe_loading.py` **3 passed**。
- 聚焦 lint：`ruff check` 对新文件通过。
- 真实样本命令：
  `python -m app.cli.audit_recipe_loading --registry-report output/source-registry/dry-run-final.json --output-dir output/prechunk-audit-v1 --force`
  （于 `apps/api` 目录执行）
- 实际 summary 关键数字：
  - `parent_records`: **979**（seed 32 + legacy 937 + sample 9 + external 1）
  - `sample_parent_count`: **9**，`sample_manifest_parent_count_ok`: **true**
  - `by_status`: loaded 41 / needs_review 938
  - `publishable_count`: **0**
  - `chunkable_count`: 411
  - 主要 `issue_counts`: STEPS_MISSING 567、QUANTITY_UNKNOWN 937、RIGHTS_UNVERIFIED 938、IDENTITY_FROM_NAME 473、IMAGE_ALT_MISMATCH 13、ALLERGENS_UNVERIFIED 10
  - 外部候选状态 `needs_review`，未进入可发布集合
  - 退出码 1（存在 severity=error 的问题，属预期；报告已完整写出）
- 未配置 `MEALFLOW_TEST_DATABASE_URL`；未对开发库 `mealflow_s7` 做迁移或破坏性测试。数据库写入未执行。

## 已知缺口

- 当前 MinerU 文本中样本 ID 仍为完整 `rload-00x`，故本轮真实报告未出现 `PARSED_ID_MISMATCH`；代码路径保留，任务书旧观测中的 `r load-001` 形态需在对应解析产物上复验。
- PDF 压缩食材行的拆条仍可能遗漏个别条目（`INGREDIENT_QUANTITY_UNCLEAR` 少量出现），需人工对照原文。
- 增量跳过依赖输出目录 `.audit_state.json`；失败重试与规则版本变化可重跑，但不会回写旧成功报告为“全部成功”。
- 未做 10,000 份容量压测；未实现 `--commit-db` 父记录入库。
- 未做真机/Android UI 验证（本任务无 UI）。
- 来源使用权与内容准确性仍需人工审核，不能由本报告自动授予发布权。