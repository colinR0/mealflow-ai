# 菜谱来源文件登记与版本扫描

日期：2026-11-07 18:18（Asia/Shanghai）

## 范围与文件

- 新增 `apps/api/app/cli/register_recipe_sources.py`，提供只读扫描和 `--commit` 登记命令。
- 新增 `apps/api/app/db/source_registry_models.py`、迁移 `h3c4d5e6f7a8_recipe_source_registry.py`，并从 `models.py` 注册模型。
- 新增 `apps/api/tests/test_recipe_source_registration.py`，验证重复扫描与内容版本递增。
- 不修改原始菜谱文件、不执行 PDF 解析、切分、向量化或发布。

## 交付行为

- 默认只扫描两份 JSON、九份多格式测试菜谱和待审核外部来源的原 PDF；MinerU 解析产物不会被重复当成来源。
- 对每个文件流式计算 SHA-256；按项目相对路径登记资产。相同摘要跳过，内容变化时追加不可变版本，并保留当前版本指针。
- `--commit` 前检查数据库处于 Alembic 当前 head；文件登记使用 PostgreSQL 事务和行锁，支持重复运行。
- JSON 文件内的单条菜谱身份将在统一加载阶段处理。旧版 937 条记录的 `bv` 存在空值和重复，不能直接作为稳定 ID。

## 使用方法

从 `apps/api/` 目录执行：

```powershell
& 'E:\APP\miniConda3\envs\langchain1.2\python.exe' -m app.cli.register_recipe_sources --report '..\..\output\source-registry\scan.json'
```

检查报告后，先核对 `DATABASE_URL`、`alembic current` 和 `alembic heads`，数据库升级到当前迁移头后再加 `--commit`。可用重复的 `--input <文件或目录>` 扫描其他项目内的来源目录。

## 验证结果

- `--dry-run` 找到 12 个原始文件，摘要读取无误。
- 在新建的 `mealflow_source_registry_20261107_test` 数据库中，Alembic 从空库升级到 `h3c4d5e6f7a8` 成功；首次 `--commit` 登记 12 个资产，第二次全部跳过。
- 临时测试文件修改后从版本 1 更新到版本 2，数据库保留两条历史版本；临时文件已清理。
- 定向 pytest：1 passed。新增文件 Ruff 与 `compileall` 检查通过。
- 全量 `ruff check apps/api` 仍报告 294 条其他位置的问题；本次未清理这些既有工作区问题。
- 为执行验证，在 `langchain1.2` Conda 环境安装仓库的 `apps/api[dev]` 依赖；`pip check` 无冲突。安装按仓库约束将 `openai` 从 3.37.0 调整到 2.54.0。

## 已知缺口

- 开发数据库当前迁移为 `f1a2b3c4d5e6`；其后还有工作区中未完成的库存迁移 `g2b3c4d5e6f7`，本次没有对开发数据库执行升级或写入。
- 此步只登记文件，不审核授权、不拆分 JSON 内的菜谱条目，也不标记已删除文件；这些属于后续加载与治理流程。
- 10,000 份文件容量尚未压测，当前验收覆盖功能正确性与小批量重跑。
