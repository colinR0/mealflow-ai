# 关键字通道按标题分级排序与菜名别名表

- 日期：2026-11-13
- 范围：`apps/api/app/services/native_retrieval.py`、
  `apps/api/app/services/dish_aliases.py`（新增）、
  `apps/api/tests/test_dish_aliases.py`（新增）、
  `apps/api/tests/test_native_retrieval.py`、`apps/api/tests/test_native_postgres.py`、
  `docs/README.md`

## 背景

上一轮把关键字通道从 `plainto_tsquery` 的 AND 语义改成 OR 精确词召回后，通道能命中，
但**排序**和**同义词**两处仍是坏的。

### 问题 1：排序退化成 `document_id` 字典序

关键字通道的排序键是 `covered DESC, rank_score DESC, document_id, chunk_id`。短查询下

- `ts_rank` 恒定：`红烧肉` 与 `勾芡` 都是 `0.060793`（单个词项在 `to_tsvector('simple')`
  里权重相同，文档长度差异不足以拉开）；
- `covered` 也恒定：单 token 查询只有 1 或 0 两种取值。

于是排序实际由 `document_id`（UUID）的字典序决定。实测 `红烧肉` 命中的 9 个 chunk 里，
真正叫「红烧肉」的文档排在第 5，前 4 个是 `南派红烧肉`（1）、`简易红烧肉`（2）、
`徽派红烧肉`（3）、`湖南家常红烧肉`（4）。

### 问题 2：口语菜名召回不到库内正名

关键字通道是纯词法匹配，用户说「番茄炒蛋」时分词为 `番茄 | 炒蛋`，而库内文档写的是
「西红柿炒鸡蛋」，词项为 `西红柿 | 炒鸡蛋`，**没有任何一个词项相同**。向量通道也只是
勉强给出 0.5261，且正确答案根本没进前 4 名：

| 排名 | 修复前 `番茄炒蛋` 返回 |
| --- | --- |
| 1 | 番茄牛肉蛋花汤 |
| 2 | 西红柿鸡蛋挂面 |
| 3 | 美式炒蛋 |
| 4 | 鸡蛋羹 |

## 改动

### 1. 标题命中分级（`keyword_matched` CTE）

用 `strpos(lower(d.title), term) > 0` 判断标题是否含查询词，而不是
`to_tsvector('simple', d.title)` —— 后者按 `simple` 配置切分中文无效，整串标题成为
**一个词项**，实测 `title_score` 全为 0（例如 `红烧肉` 与标题 `红烧肉` 都匹配不上）。

分级规则：

| `title_tier` | 条件 | 含义 |
| --- | --- | --- |
| 2 | 规范化标题 == 分词后拼接的查询词 | 标题就是这道菜（`红烧肉`） |
| 1 | 标题包含任一查询词或别名 | 这道菜的另一写法（`简易红烧肉`） |
| 0 | 仅正文命中 | 只是提到了这个词 |

排序键改为：

```sql
ORDER BY title_tier DESC, covered DESC, rank_score DESC, document_id, chunk_id
```

`exact_title` 用 `"".join(keyword_query_tokens(query))` 而不是原始 query：否则
`红烧肉怎么做` 分词后拼接得到 `红烧肉`，能与标题相等；若拿原始 query 比较，
带问法的查询永远拿不到 `title_tier = 2`。

### 2. 菜名/食材别名表（新增 `dish_aliases.py`）

`_GROUPS` 是人工维护的小表，组内成员双向可替换：`西红柿 / 番茄 / 蕃茄`、
`土豆 / 马铃薯 / 洋芋`、`黄瓜 / 青瓜`、`宫保鸡 / 宫爆鸡丁` 等。

接入点在 `native_retrieval.py`：

- `keyword_term_groups()`：每个查询词及其别名构成**一个分组**；
- `keyword_terms()`：把分组合并成 OR tsquery 的词项集合；
- `keyword_term_pairs()`：扁平成 `"<group_id>\x1f<term>"` 字符串交给 SQL。

**别名只追加、不替换**：OR 语义下追加只扩大召回，用户自己的措辞仍然参与匹配。

**覆盖度按「词分组」计数**，别名命中只算 1 次。否则 `番茄炒蛋` 扩展成 4 个词项后，
覆盖度门槛会从 ⌈2×0.5⌉=1 涨到 ⌈4×0.5⌉=2，反而更容易被拒。

**刻意不复用 `ingredient_aliases` 表**：那张表把食材映射到 canonical key
（`草鱼 → fish`），会改写 `清蒸草鱼` 的词干，让它召回一堆无关鱼类菜谱。

**别名必须是「检索词」形态，不是「表面写法」形态**：jieba 把 `宫保鸡丁` 切成
`宫保鸡` + `丁`（单字 `丁` 被丢弃），把 `木樨肉` 切成 `木樨` + `肉`。若把
`宫保鸡丁` 整串当别名塞进 tsquery，它永远等于不了入库的 `宫保鸡` 词项，是一条
**死条目**。因此表中写的是词干 `宫保鸡` / `木樨`，并由
`test_every_alias_survives_tokenization_as_one_term` 强制校验：任何被 jieba 切碎的
别名都会让测试失败，而不是静默失效。

### 3. PG 侧的一处实现约束

psycopg 无法对绑定的二维数组做下标（`CAST(:x AS text[][])[i]` 既报语法错也报类型错，
`unnest(CAST(:x AS text[][]))` 同样无法取内层数组）。因此分组改为扁平字符串数组 +
`split_part(cell, chr(31), n)` 还原，`chr(31)` 是单元分隔符，不可能出现在通过
`_KEYWORD_TOKEN` 的 token 里。

另需注意 CTE 名不能与绑定参数同名：`keyword_groups` 曾与同名参数冲突，
参数改名为 `keyword_term_pairs`。

## 验证

### 真实 512 维索引（活跃 generation，613 chunks / 462 docs）

| 查询 | top1 | 修复前 |
| --- | --- | --- |
| `红烧肉` | 红烧肉 | 第 5 |
| `红烧肉怎么做` | 红烧肉 | 第 2 |
| `番茄炒蛋` | 西红柿炒鸡蛋 | 目标未进前 4 |
| `西红柿炒鸡蛋` | 西红柿炒鸡蛋 | 第 1 |
| `宫爆鸡丁` | 宫保鸡丁 | 未命中 |
| `拍黄瓜` | 凉拌黄瓜 | — |
| `青瓜炒肉` | 黄瓜炒肉 | — |
| `马铃薯炖牛肉` | 西红柿土豆炖牛肉 | — |
| `木须肉` | 木须肉 | — |

`鸡蛋` / `豆腐` 这类高频词仍返回 5 条相关菜谱（`韩国麻药鸡蛋`、`麻婆豆腐` 等），
说明别名扩展没有把常见查询挤掉。

### 测试

- 新增 `apps/api/tests/test_dish_aliases.py`（3 条）：别名双向性、别名必须是可检索的
  单词、别名只追加不替换。
- `test_native_retrieval.py` 更新 `番茄炒蛋` 的期望为 `番茄 | 西红柿 | 蕃茄 | 炒蛋`，
  并新增 `test_keyword_term_groups_count_aliases_as_one_term`、
  `test_keyword_query_keeps_unrelated_terms_untouched`。
- `test_native_postgres.py` 新增两条 PG 用例：
  `test_keyword_channel_puts_the_dish_named_by_the_title_first`（两篇文档都命中查询词，
  且 `doc-a` 的 ID 字典序在前，验证 `title_tier` 确实覆盖了 `document_id` 排序）、
  `test_keyword_channel_recalls_library_wording_for_a_colloquial_dish`（库内写
  `西红柿炒鸡蛋`，查询是 `番茄炒蛋`，只能靠别名分组进入）。
- 检索相关 9 个测试文件合计 **163 passed、9 skipped**。
- 改动文件 `ruff check` 通过（仓库其余 297 项为既有问题，与本次无关）。

## 已知缺口

- **HNSW 索引未被生产 SQL 使用**。向量通道写了 `row_number() OVER (ORDER BY <=>
  …)`，优化器无法用它走索引扫描。改为「先纯距离 `LIMIT` 取 top-N 再排序」后可命中
  `ix_knowledge_chunks_embedding_hnsw`，但会改变 RRF 的候选集语义，本轮未做。
- **评测集已失效**。`data/eval/recipe_retrieval_dev_v1.jsonl` 的 40 条标注（34 可回答
  / 6 无答案）绑定旧语料，29 个文档 ID 在当前库中命中为 0，不能直接当作回归基线；
  需要按当前 462 篇文档重建。本次结论来自逐条人工核对，不是自动评测。
- **`清蒸草鱼` 仍 top1 错误**：库内没有草鱼菜谱（返回 `清蒸鳜鱼`）。这是语料缺口，
  不应靠别名把 `草鱼` 映射到别的鱼种——错别名会静默返回另一道菜，比返回空更糟。
- **`洋芋丝` 仍 top1 是 `拔丝土豆` 而非 `酸辣土豆丝`**：别名已正确把 `洋芋` 映射到
  `土豆`，排序问题属 `ts_rank` 对同族菜名的区分力不足，需要 IDF 或标题完全匹配的
  更细分级。
- 别名表是人工维护的小表，覆盖的是「常见同义词 + 明确误写」。不追求穷举；新增条目
  必须先确认它确实是同一道菜或同一食材。
- `鸡蛋` 命中 101 篇文档，而 `channel_limit = max(limit*8, 40) = 64`，37 篇在排序
  阶段被截断；高频词的候选截断策略本轮未调。
- 改动需重启 API 与 `python -m app.worker` 才会在运行中的服务生效。
