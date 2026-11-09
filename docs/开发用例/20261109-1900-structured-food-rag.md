# 三个结构化食材知识库：替换、风味模板、风险

- 日期：2026-11-09；用户授权实施并要求填写开发记录。
- 范围：ORM/迁移、原生与 demo 检索、持久化 Agent 工具与引用校验、导入脚本、约束 Prompt、移动端来源展示及回归测试。
- 新迁移：`j5e6f7a8b9c0`，已在独立 `mealflow_rag` 执行；数据库当前版本为 `j5e6f7a8b9c0`。

## 行为

新增三张独立表：`ingredient_substitutions`、`flavor_templates`、`food_risk_rules`。不复用 `knowledge_documents`，避免把可执行的结构化真值混入菜谱文本索引。三表共有 `review_status`、`version`、来源、`notes`、专用 `rag_text`、JSON embedding 和 `embedding_profile_id`；原生层另有 `embedding_vector vector(64)`、`search_tsv`、HNSW 与 GIN 索引。

检索先召回候选 ID 和版本，再按 ID 读取完整结构化行，并校验版本、审核状态和 embedding profile 一致。替换读取 `conversion_factor`/`unit`/饮食与排除标签；风味读取 `components`/`ratio_basis`；风险读取 `severity`/`applies_to`/年龄范围/`condition_tags`/`evidence_level`/`advice`。向量只负责召回，不能作为比例、适用性或风险等级。

三个工具为 `find_ingredient_substitute`、`get_flavor_template`、`check_food_risk`，已加入协议白名单和必填 `query` 参数。只使用 `review_status=approved` 的行；`draft`/`revoked` 不进入回答。fake embedding 阶段只接受关键词命中的候选，避免无关最近邻。

最终回答接受 `substitution:`、`flavor:`、`risk:` 引用并拒绝漏标最近一次结构化检索来源；结构化库不可用与无审核数据分别返回失败/`no_result`，不把“没查到”说成“安全”。

## 种子与导入

`scripts/structured_food_knowledge.seed.json` 包含 16 行：

- 替换 4 行，用户给出的生抽→鲜酱油、黄油→猪油、鲈鱼→多宝鱼、面筋→豆腐，按等量 `1.0`、单位“份”录入，均为 `approved`，并记录品牌咸度、素食/清真、过敏和口感限制。
- 风味模板 4 行（酸甜口、鱼香、麻辣、豉香）和风险规则 8 行（相克、发酵、卤料、亚硝酸盐、辅食、孕期、儿童、慢性病）均为 `draft` 占位，未作为正式结论启用。

导入器要求 `approved` 行必须有 `source_name`；`approved` 风险行不能是 `unverified`；`approved` 风味模板必须有 `components`。导入使用稳定 ID、内容变更递增版本、64 维 fake embedding、tsvector，并支持幂等重跑。

实际导入结果：首次 created=15，补充儿童规则后新增 1 行，当前共 16 行；替换库 4 行全部 approved 且有 64 维向量/全文词；风味库 4 行、风险库 8 行均为 draft，索引已生成但不会被检索。

## 验证

- 新增 `apps/api/tests/test_structured_knowledge_rag.py` 与 Prompt 回归；连同受影响菜谱/技能 RAG 测试共 29 passed。覆盖 native/demo 读取、fake 关键词门槛、draft 隔离、结构化字段读取、工具调用、引用漏标拒绝、导入幂等和未审核风险拒绝。
- 已对改动 Python 文件运行 Ruff，全部通过；`apps/web` 的 `npm run typecheck` 与 `npm run build:app-android` 通过，产物为 `dist/build/app`，仅有既有 Sass/Alembic 弃用警告。
- 实际查询“生抽/黄油/鲈鱼/面筋”均返回 `substitution:` 证据；“酸甜口”“孕期”返回 `no_result` 且 `empty_library=true`，未把 draft 行当作可用结论。
- 迁移和导入均在 `mealflow_rag` 实际执行，不是只跑测试库。

## 已知限制

- 仍是 fake 64 维 embedding；只能证明召回链路和结构化读取，不能证明真实语义召回质量。真实 embedding 接入后需要重新评估关键词门槛和候选排序。
- 风味模板、风险规则没有已审核来源，全部保持 `draft`，当前不会对外提供固定比例、相克结论或医学建议。替换示例虽已 approved，但“等量/份”不是精确克重，品牌咸度、过敏和饮食限制仍需用户确认。
- 未接入真实 LLM、HTTP 会话和手机界面验收；真实模型是否稳定调用三个工具、是否正确转述结构化字段尚未验证。
- 风险库不是诊断工具；孕期、婴幼儿、儿童和疾病场景只能给通用提示并建议咨询专业人员。
- 既有联网搜索兜底仍无实网成功证据，与本结构化库无关。

## 使用

在仓库根执行只读预演：

```powershell
& 'D:\APP\miniconda3\envs\mealflow\python.exe' scripts/import_structured_food_knowledge.py --source scripts/structured_food_knowledge.seed.json
```

确认后加 `--apply`。当前 `.env` 为 demo 时，导入需要临时设置 `RETRIEVAL_MODE=native`，并确保 `DATABASE_URL` 指向 `mealflow_rag`。不要对开发/演示数据库执行测试库命令。