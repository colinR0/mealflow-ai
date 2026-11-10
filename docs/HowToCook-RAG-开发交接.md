# HowToCook 菜谱研发知识库开发交接（交给 Grok 4.7）

## 任务目标与边界

在 `master` 分支，将用户提供的清洗版 HowToCook JSON 与现有 40 条已审核菜谱统一加载为父文档，按菜谱结构切片，使用本地 `BAAI/bge-small-zh-v1.5` 生成 512 维向量，写入当前 `mealflow_s7` PostgreSQL 数据库，并提供终端检索与评估报告。本轮只做本地研发知识库；聊天助手和正式用户检索不接入这批待审核数据。

用户已选定：443 条去重后的 HowToCook 菜谱 + 40 条现有菜谱、BGE small 中文版 512 维、现有业务数据库、本地终端检索验收，并要求填写开发记录。不要将 `dev` 整分支合入 `master`，也不要把同事本机 `mealflow_rag` 数据库视为可用输入。

## 输入与已核对事实

- 清洗文件：`D:\APPFiles\xwechat_files\wxid_din2ighe7eri22_3fd5\msg\file\2026-11\recipes_howtocook_mock_clean.json`；UTF-8 JSON 数组，483701 字节，SHA-256 `7156098D5DAF49FDE4C91115C376CF9EF3D4BB1B3464D1C4B25B5494271A8026`。包含 `name`、`stuff`、`difficulty`、`tools`、`category`、`heat`、`steps`。444 条输入，利用现有 `scripts/import_lazy_cookbook.py` 的 `_clean_stuff` 和 `_steps` 按「菜名 + 规范化食材 + 步骤」判重后是 443 条；食材和步骤没有空值。一组同名记录是“陈皮排骨汤”，原始 JSON 内容不完全相同，须在报告中记录判重依据。
- 现有 40 条父菜谱：`output/prechunk-audit-v1/ready_for_chunking.jsonl`；既有 47 块：`output/recipe-chunks-v1/chunks.jsonl`。应以父记录重新构建本次统一语料，不把旧 47 个 chunk 与新策略的结果无校验地拼接。
- `apps/web/public/data/recipes_howtocook.json` 是前端旧数据，共 372 条，**不是**本次 444 条清洗输入；`apps/web/public/data/recipes_merged.json` 也不是本次 HowToCook 来源。
- 当前工作区有未提交的 Qwen 离线试验文件、`.gitignore`、`.idea` 和 `data/sources/` 等改动。先查看 `git status`，保留它们；不要覆盖、删除或顺手提交。工作区现有 `models/Qwen3-Embedding-0.6B` 不代表 BGE 已安装。
- 已检查到 `.env` 指向本机 `127.0.0.1:5434/mealflow_s7`、`APP_ENV=development`。数据库 `alembic current=f1a2b3c4d5e6`，代码 `heads=h3c4d5e6f7a8`；有 5 道业务菜谱，当前活动索引是 fake 64 维，`knowledge_chunks.embedding_vector` 为 `vector(64)`，pgvector 扩展已存在。这些均为交接时快照，操作前重新核对。
- 当前可用项目解释器为 `E:\APP\miniConda3\envs\langchain1.2\python.exe`；当时缺少 `sentence_transformers`、`transformers`、`torch`。仓库 `AGENTS.md` 中另一台机器的 `D:\APP\...\mealflow\python.exe` 在本机不存在，执行前以实际环境核实。

## 实现要求

### 1. 来源登记与父文档

1. 将清洗 JSON 作为**原样、不可覆盖的本地输入**放入待审核来源区，记录路径、SHA-256、字节数、来源描述、授权状态 `unverified` 和导入规则版本。原文件不应因实现而被改写；在授权未核实前，不把原始数据提交到远端仓库。若需要共享可复现输入，只提交清单与摘要，明确运行者需自行提供同一摘要的源文件。
2. 读取 JSON 数组，显式验证记录类型、菜名、食材和步骤；生成稳定父 ID、来源记录键、内容摘要及有序步骤。保留食材原文和菜谱原始字段。去掉那 1 条经规范化后内容重复的记录，输出去重证据；跨来源同名或疑似重复只记录待审，不自动合并。
3. 与既有 40 条父记录形成统一研发语料，预期 483 条父记录。`stuff` 中的“适量”等不可推断数量；来源未给出的份数、用量、时间、每人热量和过敏原保持 `null` 或 `unknown`。不要沿用 `dev` 导入器默认 `servings=2`、由难度推时长或把 `heat` 无依据地定为每人热量的做法。`chunkable` 与 `publishable` 分开判定：有菜名、食材和完整步骤即可进入研发切分，但 HowToCook 仍 `publishable=false`。

### 2. 结构切片

复用 `apps/api/app/cli/chunk_recipe_parents.py` 的结构渲染思路，但新增**明确的研发候选入口**，不要放松原有正式 `ready_for_chunking` 的发布筛选。短菜谱生成一个 `full` 块；长菜谱生成 `overview`（菜名、类别、食材、已知元数据）与按连续步骤组成的 `steps` 块。每个步骤块带菜名和主要食材作为上下文；保留 `document_id`、`source_record_key`、来源摘要、内容版本、切分规则版本、区段与步骤序号。不要在步骤中间切断，不以固定 overlap 重复步骤。

以现有 450 字作为初始上限，同时使用 **BGE 实际 tokenizer** 检查每块输入长度；不允许模型静默截断。概览、单个步骤或上下文过长时，按字段/句子边界继续拆并保留原始步骤映射；仍不能完整容纳时记录失败、停止该记录向量化。报告父记录数、分段数、长度分布、失败原因；逐条验证步骤覆盖完整且只覆盖一次。

### 3. 数据库与向量化

在同一个 `mealflow_s7` 数据库中为研发索引建立**隔离的 staging schema/表**，用 Alembic 管理：父文档、索引构建批次、切块及 `vector(512)` 列，保存来源版本、内容摘要、切分策略、模型和 tokenizer 标识、构建状态。向量列建 cosine HNSW 索引；按需建立全文检索索引。保留业务 `recipes` 表、现有 `knowledge_*` 表、64 维向量列和活动 fake 索引。不要照搬 `dev` 的 `k6f7a8b9c0d1` 迁移：它会删除旧向量列并使当前活动 generation 失效。也不要把 443 条待审核菜谱写进业务 `recipes` 表，因为现有配餐代码会遍历该表。

本地 BGE 模型通过明确的本地路径加载；记录模型目录/版本、维度 512、查询前缀、归一化配置和切分版本。文档与问题必须使用同一模型配置。分批编码与写入，验证向量有限、非零、维度一致；按稳定 ID + 内容摘要实现幂等与断点续跑。新批次完成全部校验后才标记 `ready_for_evaluation`；失败或中断保留诊断信息，上一可评估批次不受影响。按约 1 万条来源设计批处理和进度报告，避免把所有向量一次性放进内存。

### 4. 终端检索和评估

提供仅供研发使用的只读 CLI，要求指定构建批次；输入问题，输出按父菜谱去重后的 Top K、命中的区段、相似度/融合分数、来源与版本。可用向量检索并与 PostgreSQL 全文检索作对照；不能把待审核数据接到现有 Agent 检索入口，也不能把索引标记为业务发布。检索失败须显示错误，不返回伪造的空结果。

在既有 40 条评估问句之外，增加覆盖 HowToCook 新语料的人工标注问题：按菜名、食材、做法、组合条件、否定/忌口及不可回答问题分层。报告 Hit@5、Recall@5、排序、排除违规、耗时和误召回例子；人工抽检原文→父记录→切块→检索证据。向量相似度不能代替过敏原或忌口的硬过滤；当前未知过敏原的菜谱不得用于安全承诺或三人份精确用量计算。

## 执行顺序与验收门槛

1. 先检查 `git status`、实际 `DATABASE_URL` 数据库名、`alembic current` 和 `alembic heads`；备份现有 `mealflow_s7`。在确认目标库后，从 `apps/api/` 按顺序补齐现有 `g2b3c4d5e6f7`、`h3c4d5e6f7a8`，再应用新 staging 迁移。不要绕过 API 启动迁移守卫。每次迁移后复核当前版本和业务数据。
2. 分阶段提供可重复运行的命令与报告：来源/加载 → 切片 → 向量化/入库 → 终端检索/评估。每一步核对输入摘要；前一步失败时停止后续步骤。安装依赖和模型时记录版本，不把模型权重提交到 Git。
3. 验收至少包括：444 输入、1 条可解释的去重、443 条 HowToCook 父文档、40 条既有父文档；每个成功父文档有完整来源和步骤；每个成功切块有一个 512 维真实向量，且没有模型截断或非有限值；重复运行不产生重复父文档/切块；业务库原有 5 道菜、64 维演示索引和正式助手检索行为不变。
4. 纯逻辑测试可直接运行；数据库集成测试仅使用名称以 `_test` 结尾的可丢弃 PostgreSQL，设置 `MEALFLOW_TEST_DATABASE_URL` 后运行，绝不能把 `mealflow_s7` 用作 pytest 测试库。运行 `ruff check apps/api`，单独报告既有与新增问题。真实模型评估和数据库验收结果要区分已运行、跳过与未运行，不能以 fake embedding 冒充语义质量。
5. 在 `docs/开发用例/` 新增一条 `YYYYMMDD-HHMM-brief-change-name.md`，写明修改范围、交付行为、实际验证结果及已知缺口；保留现有未提交工作区改动。最终回报文件、迁移状态、父记录/切块/向量数量、评估结果和运行命令。

## 特别提醒

这份清洗 JSON 是**结构化菜谱来源**，无需再用 MinerU 解析。加载、规范化、切片、向量化、入库是不同阶段；文件可读取或已登记，不等于完成知识库加载。`dev` 的 443 道菜与当前 `master` 的 40 条语料尚未统一，不能引用同事本机的“462 文档/462 向量”作为本轮验收结果。
