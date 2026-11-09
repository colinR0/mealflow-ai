# HowToCook 整篇单块 RAG 本地试跑

- 日期：2026-11-09。
- 用户确认：独立数据库 mealflow_rag，端口 5434，先 fake embedding，需要开发记录。
- 范围：新增 `scripts/import_howtocook_rag.py`、`scripts/start-rag-local.ps1`，更新文档入口。未修改 Agent 或检索生产代码。

## 数据与行为

输入为用户本机 `D:\LLM\agent\meal\data\清洗合并\recipes_howtocook_mock_clean.json`。444 条中，1 条与同名记录的食材、步骤相同，去重后导入 443 条 Recipe 和已发布 KnowledgeDocument。导入身份由独立 UUID 命名空间生成，只更新本导入器拥有的记录；内容变化增加 content_version。源文件不修改。

复用现有食材清洗、归一化、过敏原、口味和分类规则。保留食材原始名称并附归一化 key；共 4,055 条食材项，quantity/unit 均为 null，recipe_ingredients 不写入。原始 heat 直接写 kcal_per_serving，不除以人数。servings 默认 2，时长按难度推导，并在 content.defaults 标记。现有分类规则无法识别的 31 条写 meal_role=其他，staple_type 默认无，portion_confidence=servings_only。

KnowledgeSource 标记为 approved，授权说明明确为用户提供的本地试跑数据、原始授权待核实。fake profile 为 fake/64/v1/v1；native 模式使用 pgvector HNSW、jieba + PostgreSQL GIN，沿用原有一篇文档一个 ordinal=0 chunk 的构建器。内容未变且索引完整时重用当前 generation。

## 本地环境与运行

使用 Conda mealflow 中的 PostgreSQL 16.15、pgvector 0.8.6。未找到既有 PG 数据目录，因此新建独立集群：

- 数据目录：`C:\Users\Administrator\AppData\Local\Temp\opencode\mealflow-rag-pg16\pgdata`。
- 监听：127.0.0.1:5434；数据库 mealflow_rag；用户 mealflow。
- 随机密码保存在仓库外 `C:\Users\Administrator\AppData\Local\Temp\opencode\mealflow-rag-pg16-password.txt`，使用 SCRAM；文档和 Git 不保存密码值。
- 创建数据库前检查当前迁移状态及 heads，再执行 upgrade head；当前版本 h3c4d5e6f7a8。
- 项目 .env 保持原设置；启动脚本通过进程环境选择本次数据库及 native/fake 参数。

从仓库根目录运行：

```powershell
# 检查；集群已初始化时可自动启动
.\scripts\start-rag-local.ps1 -Mode Check

# 重新导入并验证 gateway + Agent search_recipes 工具
.\scripts\start-rag-local.ps1 -Mode Import -Source 'D:\LLM\agent\meal\data\清洗合并\recipes_howtocook_mock_clean.json'

# API 和 worker 分别在独立终端运行；对话模型仍使用项目原有配置
.\scripts\start-rag-local.ps1 -Mode Api
.\scripts\start-rag-local.ps1 -Mode Worker

# 停止本次独立集群
.\scripts\start-rag-local.ps1 -Mode Stop

# 只读导入预演
& 'D:\APP\miniconda3\envs\mealflow\python.exe' scripts/import_howtocook_rag.py --source 'D:\LLM\agent\meal\data\清洗合并\recipes_howtocook_mock_clean.json'
```

启动脚本用于本次已经初始化的集群，不是自动安装/初始化工具。临时目录可能被系统清理，需要长期使用时应另行迁移数据目录。

## 验证结果

- 首次导入新增 443 条；重复导入 created=0、updated=0，重用 generation `cdbd5cb5-96dc-418d-82aa-ea9eb57b6834`。
- 活跃索引 443 chunks / 443 distinct documents / 443 valid：ordinal=0、向量 64 维、search_tsv 非空、chunk 全文等于 document.search_text、内容版本一致。
- recipes=443，knowledge_documents=443，recipe_ingredients=0；乡村啤酒鸭 kcal_per_serving=1324，与源 heat 一致。
- HNSW cosine 和 GIN 索引均存在。
- gateway.search 和实际 Runtime.search_recipes 工具对“番茄”“乡村啤酒鸭”均返回 ok，证据含原始 steps；持久化文档步骤与 Recipe.steps 一致，所有食材用量/单位为 null。
- `ruff check scripts/import_howtocook_rag.py`：通过。
- `ruff check apps/api`：存在 294 条原有 lint 问题，本任务未修改这些文件。
- `pytest apps/api/tests/test_retrieval_gateway.py apps/api/tests/test_native_postgres.py -q`：25 passed、1 skipped；26 条 Alembic 配置弃用警告。跳过的是原生测试中针对未安装 vector 的故障场景。
- 测试使用独立普通库 mealflow_rag_plain_test 和向量库 mealflow_rag_native_test，均为可丢弃 `_test` 数据库。首次错误地共用测试库造成隔离断言失败，后改为独立数据库；先前创建的 mealflow_rag_test 也仅为临时测试库。没有在 mealflow_rag 上运行 pytest。

## 已知限制

- fake embedding 仅验证数据与检索链路；实际结果含无关菜谱，未评估语义质量或 Hit@K。
- 已验证到 Agent 工具返回 steps；未验证真实 LLM、HTTP 对话任务与移动客户端最终展示。内置 FakeProvider 不会自动发出 search_recipes 调用，不能据此声称聊天回答闭环已验收。
- 没有补充用量。归一化、过敏原和分类均为现有启发式规则，不能用于保证完整的过敏原识别；自动配餐/采购不能依赖缺失的配方用量。
- 同名但食材或步骤不同的变体保留；变体身份依赖内容，修改变体会生成新的记录，导入器不删除源文件中消失的记录。
- 切换真实 embedding 仍需实现 provider 并接入 Agent；维度变化不能依靠 ADD COLUMN IF NOT EXISTS 自动处理。
- 导入器 --apply 限制在 development 的 mealflow_rag；本地来源审批用于本次试跑，不代表已核实再分发授权。
