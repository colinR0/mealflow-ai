# RAG 检索改为向量 + 关键词 + RRF 双通道

- 日期：2026-11-13
- 范围：`apps/api/app/services/native_retrieval.py`、`apps/api/app/services/retrieval_gateway.py`、
  `apps/api/tests/test_native_retrieval.py`、`apps/api/tests/test_native_postgres.py`、
  `apps/api/tests/test_phase2_retrieval_templates.py`、`docs/README.md`

## 背景

`NativeRetriever.search()` 名义上是双通道 + RRF，但关键字通道一直不可用。
它把整句 jieba 分词结果直接交给 `plainto_tsquery('simple', :query)`，而该函数用
**AND** 连接词项，于是任何自然语句查询都要求全部词同时出现在同一个 chunk 里：

| 查询 | 修复前关键字命中 |
| --- | --- |
| `红烧肉的做法和步骤` | 0 |
| `番茄炒蛋` | 0 |
| `我想吃番茄炒蛋，家里有鸡蛋` | 0 |
| `鱼香肉丝用什么料` | 0 |

只有单 token 菜名（`红烧肉`、`鱼香肉丝`、`清蒸鲈鱼`）才有命中，双路实际退化为
“向量单路 + 后置 RRF”。

## 改动

### 1. 关键字通道改为 OR 精确词召回

`native_retrieval.py` 新增：

- `keyword_query_tokens()`：jieba 分词后去重、去停用词、去标点；
  数量词/量词短语（`一个`、`两张`、`500克`）通过 `_KEYWORD_QUANTITY` 剔除；
  当查询里存在多字词时，再丢弃单字 token（`买`、`才` 这类动词/虚词），
  但纯单字查询（`虾`、`鱼`、`葱`）完整保留。
- `keyword_query()`：拼 `a | b | c` 形式的 OR tsquery，无有效词时返回
  `_nomatch_` 并关闭该通道。

`_KEYWORD_STOPWORDS` 覆盖虚词、疑问词与做菜套话（`做法`、`步骤`、`食材`、`怎么`、
`多久` 等）。这些词在语料里几乎每个 chunk 都有（例如所有菜谱块都含「步骤1：」），
留在 OR 查询里会让通道对所有文档命中。

刻意**不使用** tsquery 权重标签（`:A` / `:B`）。`to_tsvector('simple', …)` 把所有
词项存为 weight D，带权重标签的查询因此匹配不到任何内容（实测关键字通道直接归零）。

### 2. 覆盖度门槛

OR 语义会放进“只命中一个填充词”的文档。`keyword_matched` CTE 用
`unnest(:keyword_tokens)` 统计 chunk 实际命中的查询词数 `covered`，只保留

```
covered >= GREATEST(1, CEIL(cardinality(:keyword_tokens) * 0.5))
```

的候选，并按 `covered DESC, ts_rank DESC` 排序。单 token 查询始终放行，保证
`虾` / `鱼` / `葱` 这类短食材名还能走关键字通道。

只用 `ts_rank` 阈值无法做到这一点：单个词项的 `ts_rank` 近乎恒定，
`一张`（0.0608）与 `红烧肉`（0.0608）取值相同。

### 3. 证据回传 `channels` 与 `similarity`

`NativeHit` 增加 `channels`（`keyword,vector` / 单独一路）与 `similarity`
（该 chunk 的真实余弦）。SQL 末尾用 `string_agg(DISTINCT channel)` 汇总通道，
`LEFT JOIN knowledge_chunks` 回算 `1 - (embedding_vector <=> query)`。
`retrieval_gateway.search()` 的 `ranked` 随之改为四元组，证据字典暴露
`similarity` 与 `channels`。

### 4. 下限按通道区分，并按文档计算

第一版把 0.41 直接套到融合结果上，结果误杀真实内容：`勾芡` 命中的
`做菜专业术语` 文档余弦 0.4063，被判为不相关而返回 `no_result`。

两个根因：

1. **尺度不同**。0.41 是按语义相似度标定的，精确词命中不是同一把尺子：
   真实烹饪问题的词法命中常落在 0.34–0.40，而完全无关的正文约在 0.22。
2. **粒度不同**。`similarity` 原先取“被选中证据块”的余弦，多块文档会被它
   最不相似的那一块判死。`勾芡` 所在文档共有 10 个 chunk，余弦最高 0.4751，
   但词法排序选中了 0.4063 的那一块。

改为：

- `similarity` 取该文档所有 chunk 的最大余弦（RRF 与 `channels` 本就是文档级，
  下限也应当判文档）。
- 向量通道仍用 `RETRIEVAL_MIN_SIMILARITY`（0.41）。
- 关键字通道用 `RETRIEVAL_MIN_SIMILARITY * KEYWORD_SIMILARITY_RATIO`
  （默认 0.5，即约 0.21），只挡明显无关区。新配置项为
  `keyword_similarity_ratio`，已写入 `.env.example`。

## 验证

### 关键字通道（直连 SQL，活跃 512 维 generation，613 chunks / 462 docs）

| 查询 | 词项 | 命中 chunks |
| --- | --- | --- |
| `红烧肉的做法和步骤` | `红烧肉` | 9 |
| `番茄炒蛋` | `番茄 \| 炒蛋` | 31 |
| `我想吃番茄炒蛋，家里有鸡蛋` | `番茄 \| 炒蛋 \| 家里 \| 鸡蛋` | 140 |
| `鱼香肉丝用什么料` | `鱼香肉丝 \| 料` | 21 |
| `怎么炒青菜不发黄` | `炒青菜 \| 发黄` | 6 |
| `清蒸鲈鱼要蒸多久` | `清蒸 \| 鲈鱼 \| 蒸` | 43 |
| `虾` | `虾` | 14 |
| `的了吗呢` / 空串 | 无 | 通道关闭，0 |

### 注入安全（`_KEYWORD_TOKEN` 正则清洗后）

`红烧肉&` → `红烧肉`、`红烧肉:*` → `红烧肉`、`a'b` → `a | b`、
`红烧肉<->鸡蛋` → `红烧肉 | 鸡蛋`、`!!!` → `_nomatch_`，均正常执行无语法错误。

### 端到端（`retrieval_gateway.search(..., document_type="recipe")`）

`红烧肉的做法和步骤` 8 条，`channels=keyword,vector`，余弦 0.7169–0.7499；
`清蒸鲈鱼要蒸多久` top1 余弦 0.6980；`怎么炒青菜不发黄` 出现仅 `keyword`
通道命中的相关项（余弦 0.5125）。

无关查询在覆盖度门槛 + 网关于限后归零：

| 查询 | 修复前 | 修复后 |
| --- | --- | --- |
| `今天天气怎么样` | `no_result` | `no_result`，0 条 |
| `请帮我订一张机票` | 3 条无关菜谱（余弦 0.19–0.23） | `no_result`，0 条 |
| `我想买一个垃圾桶` | 2 条无关菜谱（余弦 0.17–0.22） | 菜谱 `no_result`，0 条 |
| `python 异步编程` | — | `no_result`，0 条 |

短查询保留：`虾` / `鱼` / `葱` 各 8 / 8 / 5 条，均 `keyword,vector`。

### `no_result` 复查（按通道区分下限之后）

对 40+ 条真实用户问法逐条核对，`no_result` 只剩两种合理情形：

| 查询 | 类型 | 结论 |
| --- | --- | --- |
| `勾芡`（技能） | 有内容被误杀 | 已修复：余弦 0.4751，1 条 |
| `收汁` / `发面`（技能） | 语料没有该主题的技巧文档 | 正确，仅菜谱步骤里出现 |
| `马卡龙` | 语料没有该菜谱 | 正确 |

`发面` 在菜谱通道能命中 `无厨师机蜂蜜面包`（关键字通道，余弦 0.3421），
在技能通道返回 `no_result`，两者都符合语料事实。

### 测试

- `apps/api/tests/test_native_retrieval.py`：新增 OR 表达式构造、停用词/数量词
  剔除、单字保留、tsquery 运算符清洗、空查询关闭通道，共 22 passed。
- `apps/api/tests/test_native_postgres.py`：新增
  `test_native_reports_both_channels_and_cosine`（`channels` 与 `similarity`
  回传）、`test_keyword_channel_requires_query_term_coverage`（覆盖度门槛，
  用正交向量 + 下限隔离向量通道）、
  `test_keyword_hit_is_judged_by_best_chunk_of_the_document`（多块文档按最佳块
  判定下限），16 passed。
- 相关回归：`test_retrieval_gateway.py`、`test_recipe_rag_flow.py`、
  `test_cooking_skill_rag.py`、`test_durable_graph.py`、
  `test_phase2_retrieval_templates.py` 与两个 native 文件合计
  146 passed、9 skipped。
- 全量 `apps/api/tests`（忽略 `test_evaluate_s5.py`）：383 passed、19 skipped，
  6 个失败均与检索无关，属既有环境问题（本机时区输出 `+08:00`、
  `.env` 默认模型为 `deepseek-v4.1-flash` 而测试期望 `glm-4.5-air`、
  `CookingLogPhoto` 不接受 `created` 关键字）。
- 改动文件 `ruff check` 全部通过（仓库其余 297 项为既有问题）。

## 已知缺口

- 覆盖度门槛是启发式（≥ 一半查询词）。语料里天然高频但确有意义的词（如
  `鸡蛋` 出现在大量菜谱）仍会给关键字通道带来较宽候选，靠文档级 RRF 与
  网关下限收敛，未做 IDF 加权。
- 关键字通道仍依赖 jieba 与入库时 `search_tsv` 的分词一致性；查询词若是语料
  未切出的组合（如 `番茄炒蛋` vs 语料的 `西红柿炒鸡蛋`），只能靠向量通道找回。
- 本次未改动 `hybrid_retrieval.reciprocal_rank_fusion()` / `rrf_hits()`：
  它们只服务诊断与测试，生产链路是 `native_retrieval.py` 里的 SQL RRF。
- 检索层无法判断“用户根本不是在问做菜”。`我想买一个垃圾桶`（技能通道）仍会
  因正文出现该词而返回“如何洗碗”。这属于模型是否该调用工具的问题，已在
  `cooking_retrieval_constraints.md` 补一条：非饮食类请求不调用检索工具，
  并区分“知识库未覆盖”与“检索失败”。不在检索层加关键词门（AGENTS.md 禁止）。
- 修复 `test_phase2_retrieval_templates.py` 一处旧问题：测试数据往 JSON 列写入
  `Decimal` 导致 `TypeError`，改为数值字面量；同时删除未使用的 `workspace_id`。
