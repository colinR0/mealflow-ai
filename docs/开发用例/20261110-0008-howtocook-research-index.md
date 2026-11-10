# HowToCook 研发知识库

日期：2026-11-10 00:08（Asia/Shanghai）

## 范围与文件

在 `master` 上把清洗版 HowToCook 与既有 40 条父菜谱做成隔离研发索引。未合并 `dev`，未改业务 `recipes` 和 `knowledge_chunks` 的 64 维列，也未接入聊天助手。

- 新增加载、切片、嵌入、检索命令：`apps/api/app/cli/load_howtocook_research.py`、`chunk_research_recipes.py`、`embed_research_recipes.py`、`search_research_recipes.py`
- 新增 `apps/api/app/services/research_recipes.py`、`research_chunks.py`、`research_index.py`
- 新增迁移 `apps/api/migrations/versions/l7a8b9c0d1e2_recipe_research_staging.py`，`down_revision` 为 `h3c4d5e6f7a8`
- 新增 `apps/api/tests/test_howtocook_research.py`、`data/eval/recipe_research_howtocook_v1.jsonl`
- 来源清单 `data/sources/pending/howtocook-mock-clean/manifest.json`。原始 JSON 已忽略，不提交
- 正式切分入口 `chunk_recipe_parents.py` 仍要求 `publishable_candidate`

保留了工作区里原有的 Qwen 试验、`.idea`、lin-chef 待审目录、个人文档和浏览器配置。

## 交付行为

- 444 条输入按「菜名 + 规范化食材 + 步骤」去掉 1 条重复的陈皮排骨汤（原文摘要不同：`174b7a3f...` 保留，`b3012303...` 去掉），得到 443 条。与 40 条既有父文档合成 483 条。6 个跨来源同名只记录、不合并。
- HowToCook 份数、用时、热量、过敏原保持空/`unknown`。来源里的 `heat` 原样保存，没有当成每人热量。`publishable=false`。
- 482 条父文档切成 741 块（full 361、overview 121、steps 259）。最长 450 字、430 token，没有静默截断。`牛油火锅底料` 的第 1 步无法按句子拆开，已记录且未向量化。
- `mealflow_s7` 先备份到 `output/backups/mealflow_s7-20261110-0005.dump`（158699 字节），再从 `f1a2b3c4d5e6` 升到 `g2b3c4d5e6f7`、`h3c4d5e6f7a8`、`l7a8b9c0d1e2`。研发数据在 `recipe_research`，向量为 cosine HNSW 的 `vector(512)`。
- 批次 `286bfcc1-52d1-451c-b126-62295cc22259` 状态 `ready_for_evaluation`，741 条 512 维向量，没有非有限值或零向量。重复执行命中同一批次，没有再写入。`production_publishable` 保持 false。

## 验证与结果

- `pytest --noconftest tests/test_howtocook_research.py`：12 通过。没有使用 `mealflow_s7` 做测试库。
- 新增文件 `ruff check` 通过。整个 `apps/api` 仍有 294 条既有 ruff 问题，不在本次文件里。
- 迁移后业务菜谱仍是 5 道，`knowledge_chunks.embedding_vector` 仍是 `vector(64)`。
- 模型是本地 `BAAI/bge-small-zh-v1.5`，查询前缀为「为这个句子生成表示以用于检索相关文章：」。不是 fake embedding。
- 新 12 题向量检索：Hit@5 0.9091，Recall@5 0.8462，Precision@5 0.2，MRR@10 0.8636。全文对照：Hit@5 0.7273，Recall@5 0.6154，Precision@5 0.1455，MRR@10 0.553。平均 92.6 ms。
- 既有 40 题在扩大后的语料上：向量 Hit@5 0.8824，Recall@5 0.7045，Precision@5 0.1824，MRR@10 0.7574。全文对照 Hit@5 0.5882。
- 「不要花生」「花生过敏」仍召回含花生的菜。这是排除违规，不是安全过滤。

## 已知缺口

- 清洗数据授权仍是 `unverified`，这批索引不能发布，也不能接入 Agent。
- `牛油火锅底料` 没有向量。
- HowToCook 过敏原未知，不能做过敏原承诺或按份数精确换算。
- 向量相似度没有实现忌口硬过滤。
- 未把结果写成可对外的质量结论。
