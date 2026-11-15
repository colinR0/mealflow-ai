# PostgreSQL 恢复与冗余结构清理

- 日期：2026-11-15
- 范围：从损坏的 5434 实例导出可读数据，在带校验和的新实例重建 `mealflow_s7`，删除已退役的 V1 结构，并切回 5434。
- 记录授权：用户确认的恢复方案要求新增本记录。

## 文件

新增一次性导出和导入工具 `apps/api/app/cli/export_recoverable_data.py`、`import_recovered_data.py`，以及清理迁移 `apps/api/migrations/versions/m8c9d0e1f2a3_drop_retired_structures.py`。

收敛运行时代码：`apps/api/app/db/models.py` 只保留账户和食材字典，`v2_models.py` 去掉 64 维知识索引，`seed.py` 只为测试库准备家庭和食材，`router.py` 不再挂 V1 路由。删除 V1 库存、家庭、共享数据路由和服务，以及 `seed_s7_demo.py`、64 维检索、旧评估脚本和对应测试。`scripts/import_lazy_cookbook.py` 把已退役 ORM 的导入移进实际写库函数，切分脚本仍可被研发语料清洗调用。`scripts/start-s7-demo.ps1` 和 `docs/05_Windows依赖与启动手册.md` 改为新实例路径，并启用 `--data-checksums`。

本机 Cursor 规则只改私有文件，不写入 `AGENTS.md`。备份、故障现场和恢复报告在 `%LOCALAPPDATA%\MealFlow\backups`，不进 Git。`.env` 已指向新实例，该文件不提交。

## 交付行为

新实例位于 `%LOCALAPPDATA%\MealFlow\postgres16`，数据目录 `pgdata`，监听 `127.0.0.1:5434`，数据校验和打开。角色口令只在本机口令文件和 `.env`。Alembic 版本是 `m8c9d0e1f2a3`。自定义表加上 `alembic_version` 共 27 张。

研发语料没有重新切分或重新向量化。483 条父菜谱、741 条切片和 1 个构建批次来自损坏库的当前导出。未切分的父菜谱 `c4fba75b-7311-54c4-a7b7-d034756bb80e` 仍在。741 条向量都是非空 512 维，逐条摘要与导出清单一致。

账户保留原 ID 和原密码哈希。`17737771167` 可以重新登录。登录会话和失败计数按规则未恢复。库存、采购、食材和部分审计来自 2026-11-10 00:03 快照，之后的变更无法确认。家庭成员和档案保留故障后补入的行，并用快照补上不冲突的历史行。16 个后台任务原本都是完成状态，没有重新执行；导入时去掉了迁移之外的 `native_generation_id`。待确认动作没有在恢复过程中写采购或库存。

5 条业务菜谱、旧 64 维知识索引和 LangGraph checkpoint 只进离线归档，不进新运行库。

## 验证与结果

- 导出清单：父菜谱 483、切片 741、空向量 0、零向量 0、512 维非空向量 741。
- 新库行数与清单一致。向量 `md5(vector_send(embedding))` 与导出清单逐条一致，汇总摘要 `5426ebf079fb1c79751a92dbbe68b5cc`。HNSW 与 GIN 已重建，切片序列停在 741。
- `REINDEX DATABASE mealflow_s7` 完成。外键检查没有失效引用。应用启动时的 Alembic 版本检查通过。
- 新库 `pg_dump` 恢复到一次性的 `mealflow_s7_test` 后，父菜谱、切片、账户和库存批次分别为 483、741、2、4，向量汇总摘要相同。演练库随后只作为 pytest 用库。
- `apps/api/tests` 在 `mealflow_s7_test` 上 144 通过、0 失败。变更过的 Python 文件 `ruff check` 通过。
- API `http://127.0.0.1:8000/api/v1/health` 返回 `ok`。`17737771167` 登录返回原用户 ID。worker 已启动。

逐表来源见 `%LOCALAPPDATA%\MealFlow\backups\20261115-damaged-export\recovery-report.json`。成功备份是 `%LOCALAPPDATA%\MealFlow\backups\20261115-mealflow_s7-restored.dump`。损坏数据目录的副本在 `%LOCALAPPDATA%\MealFlow\backups\20261115-damaged-cluster`，它不是可直接启动的备份。

## 已知缺口

- 2026-11-10 快照之后、故障前的库存、采购、食材和审计变更无法确认。快照里食材只有 7 条，库存批次 4 条。
- 当前库读不出来、快照里也为空的旧表没有可恢复行，报告标为未恢复。
- 登录会话和失败计数故意不恢复，需要重新登录。
- 5 条业务菜谱只在导出包里，不在新运行库。
- `%TEMP%\mealflow-pytest-pg` 和 `%TEMP%\mealflow-tools-pg-20261114` 已删除。旧数据目录已删除；`mealflow-pgvector16\Library\bin` 里还有 5 个被其他进程锁住的运行库文件，不影响新实例。
- 不要对 `mealflow_s7` 再跑导入脚本或 `seed_demo_data`。不要把会删表的 pytest 指向 `mealflow_s7`。
- 代码尚未提交。
