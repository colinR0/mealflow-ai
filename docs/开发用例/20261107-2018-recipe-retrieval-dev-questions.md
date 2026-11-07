# 菜谱切分结果与开发期检索问题集

日期：2026-11-07 20:18（Asia/Shanghai）

## 范围与文件

- 记录已可运行的切分命令 `apps/api/app/cli/chunk_recipe_parents.py` 及其测试 `apps/api/tests/test_chunk_recipe_parents.py`。切分结果在 `output/recipe-chunks-v1/`。
- 新增开发问题集 `data/eval/recipe_retrieval_dev_v1.jsonl`。
- 新增只读评分命令 `apps/api/app/cli/score_recipe_retrieval.py` 和测试 `apps/api/tests/test_score_recipe_retrieval.py`。
- 评分报告写在 `output/recipe-retrieval-dev-v1/`。没有改原始菜谱，没有做 embedding，也没有写数据库。

## 交付行为

- 切分读取 40 条切分候选。不超过 450 字的菜保持整菜一块；更长的菜先保留概览，再按完整步骤分组。单步超长会失败，不截断半步。
- 这次实际结果是 40 道菜、47 块：33 块 `full`，7 块 `overview`，7 块 `steps`，0 条错误。短的种子菜没有被拆开。
- 问题集共 40 条，只覆盖这 40 道菜，分成查菜 12 条、问做法 14 条、按条件找菜 14 条。每条写明可接受菜谱、必须排除的菜、是否可回答，以及证据应落在 `full`、`overview` 还是 `steps`。其中有别名、否定和无答案。
- 字符串评分按菜名、食材、设备和类别做重叠，同一道菜只保留得分最高的一块。否定词不参与打分。报告给出 Hit@5 和 MRR@10，并标明这不是检索质量结论。

## 验证与结果

- 切分测试：`pytest --noconftest tests/test_chunk_recipe_parents.py -q`，3 passed。
- 评分测试：`pytest --noconftest tests/test_score_recipe_retrieval.py -q`，4 passed。
- 聚焦 lint：`ruff check app/cli/score_recipe_retrieval.py tests/test_score_recipe_retrieval.py`，通过。
- 对现有 `output/recipe-chunks-v1/chunks.jsonl` 跑了评分命令。34 条可回答问题的 Hit@5 为 0.8824，MRR@10 为 0.8407；证据区块吻合比例为 0.7941。5 条问题把必须排除的菜排进了前 5，2 条无答案问题仍返回了菜。
- 精确菜名 10 条全部命中。没能靠字符串解决的包括“西红柿炒蛋”“宫爆鸡丁”“清淡一点的鱼”，以及“不要花生米”只召回了含花生的菜。问做法时，同名的概览块和步骤块得分相同，当前会留下概览，所以“什么时候放虾/冬瓜”虽然找到了菜，证据区块并不符合预期。

## 已知限制

- 这 40 道菜仍不是生产可发布集合；`publishable_count` 仍为 0。字符串分数不能当作全文或向量检索的质量。
- 冻结问题集还没有单列。设计里的 40 条冻结题要等菜谱变多后再写，避免和这 40 条用同一批菜调参。
- 没有生成向量，没有写入 pgvector，也没有把这些块接到助手。
