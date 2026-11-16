# RAG 多块索引与 480 token 重建

- 日期：2026-11-10 19:38
- 范围：把菜谱/技能索引从每文档单 chunk 改为结构化多 chunk，修复超长文档被 embedding 模型静默截断；重建开发库索引并按新分布重标相关性阈值。
- 环境：`mealflow_rag`（开发库）、`mealflow_rag_plain_test`、`mealflow_rag_native_test`；模型 `BAAI/bge-small-zh-v1.5`（512 维）；active generation `2e200a38-6733-43db-b5f0-153d10da6f2a`，profile `emb-61e64187420f54ed112d247317cb735b`。

## 改动文件

- `apps/api/app/services/text_chunking.py`（新增）：`MAX_TOKENS=480`、`OVERLAP_TOKENS=60`；菜谱按“食材集合 + 每个步骤”、技能按 Markdown 段落/句子切分，超长单元按 token 预算硬切；每个 chunk 重复文档标题；空文档回退为标题。
- `apps/api/app/services/local_embedding.py`：新增 `count_tokens()`，与真实 embedding tokenizer 一致（含特殊 token）。
- `apps/api/app/services/native_index_worker.py`：按文档多 chunk 写入，`ordinal` 从 0 递增；标题优先取 `knowledge_documents.title`，为空时回退 `content.name/title`；重建/索引任务传递 `count_tokens`。
- `apps/api/app/services/native_retrieval.py`：keyword/vector 两路先按 `document_id` 取最佳 chunk，再按文档 `doc_rank` 进入 RRF，避免单文档多 chunk 挤占其他文档。
- `apps/api/app/services/retrieval_gateway.py`：demo 路径同样按文档去重后再截 Top40；native 真实模型传入阈值；fake 保持 0 以保留关键词闸门语义。
- `scripts/rebuild_rag_index.py`（新增）：强制新建 generation，校验配置 provider 与当前 active profile 一致后重建，失败保留旧 generation。
- `scripts/import_howtocook_rag.py`：导入时回填菜谱 `title`，重建时传入 tokenizer 计数。
- `scripts/start-rag-local.ps1`：新增 `Rebuild` 模式。
- `apps/api/app/core/config.py`、`.env.example`：`RETRIEVAL_MIN_SIMILARITY` 默认值 0.40 → 0.41。
- `apps/api/tests/test_text_chunking.py`（新增）、`test_native_postgres.py`：覆盖切分上限/重叠/标题，以及 45 个 `doc-a` chunk 不再挤掉 `doc-b` 的回归。
- 数据：开发库 443 条菜谱 `knowledge_documents.title` 由 `recipes.name` 回填后重建。

## 行为

- 单块（含标题与特殊 token）不超过 480；仅当上一单元不超过 60 token 且能与下一单元同块时复用，作为约 60 token 重叠；不做跨块 token 级滑窗。
- 菜谱块保留“食材：…”与“步骤N：…”；技能块优先使用 `content.markdown`，按空行/标题/句末标点切分。
- 检索排序改为文档级：每文档只保留 keyword/vector 各自最佳 chunk，按文档名次计算 RRF；同一文档的多个命中不会重复占位。
- 重建脚本用于内容/切分/模型变更后的显式重建；`ensure_generation` 的“缺 chunk 才建”逻辑未改。
- 阈值在最终索引上用真实 BGE 复测：0.40 下“怎么理财”以 0.4059 命中“如何洗碗”，0.41 下返回 `no_result`；`生抽`（向量 0.388，关键词命中）、`番茄炒蛋`（0.6651）、`焯水多久`（0.6396）、`怀孕能吃什么`（0.4766）仍可用。

## 验证

- `ruff check` 通过（改动 Python 文件）。
- `test_text_chunking.py`：4 passed。
- `test_native_postgres.py`：12 passed，含多 chunk 与文档去重回归。
- `test_retrieval_gateway.py` + `test_structured_knowledge_rag.py`：26 passed, 1 skipped。
- gateway + skills + recipes + demo 组合：35 passed, 1 skipped。
- 重建后 active generation `2e200a38…`：613 chunk / 462 文档；max 480 token；0 个 >480，0 个 >512；recipe 518、cooking_skill 95；81 篇多 chunk，单文档最多 19 chunk；613 个 chunk 全部以标题开头。
- 旧索引对照：462 chunk 中 46 个（46/462=9.96%）超过 512 token，最长 8104；技能 19 篇中 16 篇超限。
- 回填后 443 条菜谱标题无空值；重建前后最佳 cosine 一致，说明 chunk 标题此前已由 `content.name` 回退生成，标题列回填修复的是 evidence 元数据。

## 已知缺口

- `生抽放多少` 仍返回 `no_result`：关键词 `plainto_tsquery` 对分词做 AND，`多少` 无法命中；向量 0.3993 低于 0.41。需要查询归一化或 reranker，不能靠继续降阈值解决。
- “怎么理财” 0.4059 距 0.41 仅 0.0041，阈值余量小；需要更多域外探针和持续评估。
- 0.41 同时作用于结构化知识向量通道，本次未单独复测其真实向量分布；如结构化召回下降，应分开配置阈值。
- reranker 未实现；`evaluate_s5.py` 仍未实际打分；`ts_rank` 仍非 `ts_rank_cd`。
- chunk id 每次重建都会变化，历史引用应使用 `document_id + content_version`。
- 单文档最多 19 chunk，超长菜谱仍有冗余；未实现 parent-child/摘要检索。
- 菜谱标题回填是开发库一次性 SQL；其他环境需重跑导入或等价回填后再 `scripts/rebuild_rag_index.py`。`ensure_generation` 不会因标题变化自动重建。
- 首次网络受限重建留下 1 条 failed generation 记录，未清理；不影响 active 索引。