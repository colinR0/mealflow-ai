# RAG 检索链路验证与相关性闸门

- 日期：2026-11-09 23:40
- 范围：先只读验证 native + BGE 512 的完整检索链路，随后实施 P0 修复（相关性闸门、失效测试、导入幂等）。
- 环境：`mealflow_rag`（开发库）、`mealflow_rag_plain_test`、`mealflow_rag_native_test`；模型 `BAAI/bge-small-zh-v1.5`（512 维）。

## 改动文件

- `apps/api/app/core/config.py`：新增 `retrieval_min_similarity`（默认 0.40，仅查询期生效，不参与 embedding profile 身份）。
- `apps/api/app/services/native_retrieval.py`：文档向量通道增加 cosine 下限；keyword 通道保持精确词命中。
- `apps/api/app/services/structured_knowledge.py`：结构化检索向量通道增加同一下限，证据附带 `similarity`，低于下限时返回 `no_result` 并给出说明。
- `apps/api/app/services/retrieval_gateway.py`：native 检索传入下限，结果标记 `min_similarity`；无证据时说明原因。
- `scripts/import_structured_food_knowledge.py`：`conversion_factor` 改用 `Decimal` 解析，修复重跑导入时的假更新。
- `apps/api/tests/test_structured_knowledge_rag.py`、`test_native_postgres.py`、`test_retrieval_gateway.py`：修正被 51 行种子打破的断言，新增闸门与幂等测试。
- `.env.example`、`docs/README.md`、`apps/api/app/db/v2_models.py`：配置项与过时描述。

## 行为

- 向量候选满足 `1 - (embedding <=> query) >= RETRIEVAL_MIN_SIMILARITY` 才进入 RRF；无关查询因此返回 `no_result`，不再把最近邻当作证据。
- `fake` embedding 继续走 `keyword_hit` 门槛并跳过相似度下限，保证测试确定性；真实 provider 才启用下限。
- 结构化证据新增 `similarity` 字段，回答侧仍只允许引用本轮证据，`MISSING_STRUCTURED_CITATION` 规则不变。
- 文档证据的 `score` 仍是 RRF 名次分，不改为相似度，避免影响既有排序契约。
- 导入器对 `Numeric(6,4)` 字段使用 `Decimal` 比较，重复执行导入返回 `{"created": 0, "updated": 0}`。

## 标定数据（同一 profile）

结构化相关查询 top1 ≥ 0.4307（`酸甜口`），无关查询 top1 ≤ 0.3834（draft-only 的 `醋`）；文档相关 top1 ≥ 0.4682（`焯水` 的第二条，top1 为 0.6227），无关 top1 ≤ 0.3748（`今天天气怎么样`）。据此取默认下限 0.40。

| 类别 | 相关示例 | 无关示例 |
|---|---|---|
| substitution | 生抽 0.4789、黄油 0.5272、鲈鱼 0.5472、面筋 0.5622 | 电视机 0.2437、股票 0.3012、天气 0.2919 |
| flavor | 酸甜口 0.4307、鱼香 0.5384、麻辣 0.5359 | 电视机 0.2404、股票 0.3164、天气 0.3574 |
| risk | 孕期 0.5304、婴幼儿 0.4715、亚硝酸盐 0.5131 | 电视机 0.2341、股票 0.3334 |
| 文档 | 番茄炒蛋 0.5939、清蒸鲈鱼 0.6131、焯水 0.6227 | 股票走势 0.2807、天气 0.3748、英文 0.3488 |

换模型、换分块策略或改 `rag_text` 后必须重新标定，不能沿用 0.40。

## 验证

```powershell
$env:MEALFLOW_TEST_DATABASE_URL='postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_rag_plain_test?gssencmode=disable'
$env:MEALFLOW_TEST_NATIVE_POSTGRES_URL='postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_rag_native_test?gssencmode=disable'
& 'D:\APP\miniconda3\envs\mealflow\python.exe' -m pytest apps/api/tests/test_structured_knowledge_rag.py apps/api/tests/test_retrieval_gateway.py apps/api/tests/test_recipe_rag_flow.py apps/api/tests/test_cooking_skill_rag.py apps/api/tests/test_local_embedding.py apps/api/tests/test_cooking_retrieval_prompt.py apps/api/tests/test_native_retrieval.py apps/api/tests/test_native_postgres.py -q
```

结果：`70 passed, 1 skipped`。跳过项是既有的 `MEALFLOW_TEST_POSTGRES_URL` 故障测试，与本次改动无关。改动文件 Ruff 检查通过（仓库其它文件存在历史 Ruff 问题，未一并处理）。

真实 BGE 复测（`mealflow_rag`，真实 provider）：

| 查询 | 结果 |
|---|---|
| 生抽 | ok，2 条，similarity 0.4789 |
| 醋（仅 draft） | no_result，提示未达下限 |
| 今天天气怎么样 / 股票 | no_result |
| 酸甜口 | ok，1 条，0.4307 |
| 孕期 | ok，1 条，0.5304 |
| 菜谱：番茄炒蛋 / 焯水 | ok |
| 菜谱：股票未来走势 / 今天天气怎么样 | no_result |

## 已知缺口（未在本次处理）

- 替换组仍存在同 key 多行，组行 `rag_text` 写的系数与行字段不一致；分块后需一并收敛并重标阈值。
- 462 个 chunk 中 46 个（46/462=9.96%）超过模型 512 token 上限，最长 8104 token；技巧 19 篇中 16 篇超限；仍是每文档单 chunk（已由 20261110 分块重建修复）。
- 443 条菜谱文档 `title` 为 NULL，移动端来源卡取不到菜名（已回填 443 条并重建）。
- 生产环境没有自动重建索引入口；改菜谱/技能内容后仍需手动重跑导入。
- `emb-994626…`（Temp 绝对路径）历史 profile 与失活 generation 仍保留，需治理。