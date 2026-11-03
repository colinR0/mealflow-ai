# S5 原生检索交接

本变更新增独立模块，不修改现有 `worker.py`、`v2_runtime.py`、路由或 Alembic
迁移，避免与 S4 迁移产生第二个 head。

## 安装 PostgreSQL 索引结构

在 `apps/api` 且 `DATABASE_URL` 指向 PostgreSQL 时执行：

```powershell
..\..\.venv\Scripts\python.exe scripts/install_s5_native_schema.py
```

安装内容包括 `vector` 扩展、`background_jobs.native_generation_id` 结果列、
`knowledge_chunks.embedding_vector`、
`knowledge_chunks.search_tsv`、HNSW cosine 索引和 GIN 全文索引。中文文本由
`jieba` 预分词后使用 PostgreSQL `simple` 配置建立 `tsvector`。

原生集成测试显式 opt-in，使用随机临时 schema，先 `Base.metadata.create_all()`，
再安装 native 列和索引；结束时只删除该测试 schema。缺少环境变量或数据库没有
vector 扩展时明确 skip，不将其计为原生验证通过。SQLite 会拒绝 native 安装和构建。

## 索引与检索

`app.services.native_index_worker.rebuild_generation` 接受空闲 `Connection`，自行管理
短事务；不要放在 `engine.begin()` 中调用。文档快照、逐 chunk 写入和最终切换各自
使用短事务，外部 embedding 调用期间没有数据库事务。同 profile 的构建由 session
advisory lock 排他保护，重复并发调用明确报 `already running`；数据库连接断开后锁
自动释放。最终切换另取全局 transaction advisory lock，与 ORM 当前全局单 active
generation 约束一致（跨 profile 构建也只保留一个 active）。只有完成构建才原子关闭
旧 generation、激活新 generation；失败保留旧 active 并将新 generation 标记 failed。

构建前校验 provider 的 `model`、`fake` 与 profile 一致，并校验每个向量的维度和
有限数值（拒绝 bool、NaN 和 Infinity）。默认 fake provider 对应 `model='fake'`、
64 维、`is_fake=true`；自定义 provider 必须显式传入 `model`。激活前重新以行锁
读取 profile，检测 model/dimensions/fake/template/tokenizer 变更并拒绝过时构建。
查询 SQL 同时执行：

- active、ready generation 过滤；
- source `approved`、document `published`、当前 `content_version` 过滤；
- PostgreSQL tsvector 关键词通道与 pgvector cosine 通道；
- 每通路按父 recipe 保留最佳 chunk rank，再融合两路 RRF，避免多块父菜重复加权；
- 从最佳 rank 的单条真实候选行选取完整 chunk/excerpt/source/version，不用独立 max 拼接。

撤销批准或修改文档版本后，查询条件立即使旧证据不可见，不需要等待下一次
索引构建。`process_index_job` 仅处理 `background_jobs.kind =
'knowledge_rebuild'`。该适配器自行领取 job，以 session advisory lock 去重，领取事务
另取 job 行锁并递增 attempts；完成后重复调用返回持久化的 native_generation_id。
激活 generation 和 job succeeded 在同一事务提交，避免崩溃后重复发布。异常标记
failed，可重新调用；连接崩溃留下的 running job 可由取得同一 session lock 的调用
恢复。此约定要求 native job 只由此适配器领取，不能与其他不遵守该锁的 worker 混用。

## 真实 PostgreSQL 验证

```powershell
$env:MEALFLOW_TEST_NATIVE_POSTGRES_URL = 'postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_vector'
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_native_retrieval.py apps/api/tests/test_native_postgres.py -q
```

本次在 PostgreSQL 16.15 + pgvector 0.8.6 上执行结果为 **19 passed**（其中 10 项
真实 PostgreSQL 测试，无 skip）。覆盖 HNSW/GIN 安装、真实双通路 SQL、父菜最佳 rank
分数和证据一致性、版本变化、审批撤销、provider 无长事务、profile 校验及构建中变更、
失败保留旧 generation、job 重试/结果去重/遗留 running 恢复、同 profile 并发排他和
跨 profile 并发切换。测试使用 fake 向量和受控语料，验证数据库行为，不代表语义质量。

## 离线评测

`data/s5_eval_queries.json` 固定包含 40 条 `dev` 和 40 条 `frozen` 查询。
`scripts/evaluate_s5.py` 默认不调用网络或收费 embedding。当前数据集只有人工相关
标签，没有真实语料排名，因此脚本输出 `dataset_ready_not_scored`；不能将其解释
为检索质量分数。若后续接入授权 embedding 和真实索引，应把每条查询的
`ranked_recipe_ids` 写入副本后再计算 Hit@K/MRR，并保留 provider、profile 和
语料版本。
