# 菜谱、技能与结构化知识切换到本地 BGE 512 维向量

- 日期：2026-11-09；用户要求本次开发填写开发记录。
- 范围：本地 embedding provider、检索 profile 校验、64→512 迁移、菜谱/技能/结构化知识重建与召回验证、相关回归测试。
- 代码提交：`bfefba2 feat: 接入菜谱技能与结构化知识 RAG，并支持本地 BGE 向量`。

## 行为

新增 `apps/api/app/services/local_embedding.py`，使用 `sentence-transformers` 在本地运行 `BAAI/bge-small-zh-v1.5`，输出 512 维单位向量。模型采用懒加载，demo 模式和单元测试不会导入 torch 或下载权重；缺少 `sentence-transformers`、模型加载失败、维度不符、向量含非有限值或零向量都会明确报错。

查询文本在编码前加 BGE 中文检索前缀 `为这个句子生成表示以用于检索相关文章：`，文档文本不加前缀。配置项包括 `RETRIEVAL_EMBEDDING_MODEL`、`RETRIEVAL_EMBEDDING_DIMENSIONS`、`RETRIEVAL_EMBEDDING_QUERY_PREFIX`、`RETRIEVAL_EMBEDDING_DEVICE`、`RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY`、`RETRIEVAL_EMBEDDING_MAX_SEQ_LENGTH`。默认值仍为 `fake`、64 维、demo 检索，测试继续强制 fake provider，避免测试依赖模型文件或网络。

检索入口按配置构建 provider，并校验活动索引 generation、embedding profile、模型、维度、模板版本和分词版本。profile 不一致时返回 `EMBEDDING_PROFILE_MISMATCH`；demo 模式使用真实模型时返回 `DEMO_REQUIRES_FAKE_PROVIDER`。真实向量与旧 fake 向量不会混用。

## 迁移与重建

新增迁移 `k6f7a8b9c0d1_bge_small_zh_512`，将 `knowledge_chunks`、`ingredient_substitutions`、`flavor_templates`、`food_risk_rules` 的 `embedding_vector` 从 `vector(64)` 改为 `vector(512)`，重建 HNSW cosine 索引，清空旧 JSON 向量并失效原活动 generation。

本地 `mealflow_rag` 已从 `j5e6f7a8b9c0` 升级到 `k6f7a8b9c0d1`。使用本地模型重建菜谱/技能索引，活动 generation 为 `ead6c923-8d48-4d1e-8d7c-196d418966d1`，462 个文档、462 个 chunk，均为真实 512 维向量；结构化知识当时按已提交的 16 行种子重建，替换 4 行 approved，风味 4 行和风险 8 行保持 draft。

模型从 ModelScope 下载到仓库外目录，未提交模型权重。`model.safetensors` 的 SHA256 为 `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`。运行时 profile 的 `model` 字段保存模型绝对路径，因此导入、检索和 API/Worker 必须使用同一路径；改用默认模型名会触发 profile mismatch。

## 验证

- 本地模型加载输出 512 维单位范数；查询“生抽没有了，能用什么替代？”与生抽相关文本相似度约 `0.62`，高于黄油 `0.3295` 和鲈鱼 `0.2987`。
- 数据库四张表均为 `vector(512)`，HNSW 索引存在，旧活动 generation 已失效。
- 菜谱召回：番茄炒蛋命中西红柿炒鸡蛋，清蒸鲈鱼精确命中，黄油煎牛排命中牛排。
- 技能召回：焯水命中《焯水》《去腥》《做菜专业术语》；油温命中《油温判断技巧及常见温度和单位换算表》。
- 结构化召回：生抽→鲜酱油、黄油→猪油、鲈鱼→多宝鱼、面筋→豆腐均命中 approved 行；风味和风险查询返回 `no_result` 且 `empty_library=true`，draft 未泄露。
- 测试结果：`test_local_embedding.py` 4 passed；`test_retrieval_gateway.py` 13 passed、3 skipped；`test_cooking_skill_rag.py` 9 passed、6 skipped；`test_structured_knowledge_rag.py` 1 passed、7 skipped；`test_recipe_rag_flow.py` 4 skipped。跳过项需要真实 PostgreSQL+pgvector 测试环境。

## 已知限制

- 模型文件位于仓库外，部署时必须预置模型并显式配置同一路径；当前未提供自动下载或容器镜像内置模型。
- Hugging Face 和 hf-mirror 在当前网络下超时，模型通过 ModelScope 获取；这不影响运行时本地推理，但下载步骤需要单独说明。
- 默认配置仍是 demo + fake 64 维。只有设置 `RETRIEVAL_MODE=native`、真实模型路径和 512 维后才使用 BGE；未完成真实 LLM 回答质量和手机界面验收。
- native pgvector 测试和 `test_cooking_retrieval_prompt.py` 本轮未取得有效执行结果，不能引用为通过。
- 当前工作区另有 `scripts/structured_food_knowledge.seed.json` 的大规模未提交扩展，包含新增 approved/draft 行，不属于本次 BGE 改动，未纳入本记录和提交。

## 本地运行

```powershell
$env:APP_ENV='development'
$env:RETRIEVAL_MODE='native'
$env:RETRIEVAL_EMBEDDING_MODEL='C:\Users\Administrator\AppData\Local\Temp\opencode\models\bge-small-zh-v1.5'
$env:RETRIEVAL_EMBEDDING_DIMENSIONS='512'
$env:RETRIEVAL_TEMPLATE_VERSION='bge-zh-query-v1'
$env:RETRIEVAL_TOKENIZER_VERSION='v1'
$env:RETRIEVAL_EMBEDDING_LOCAL_FILES_ONLY='true'
```

安装本地向量依赖使用 `.\apps\api[local-embeddings]` extra，并在 `apps/api` 下启动 API 和 worker。重建索引使用 `scripts/import_howtocook_rag.py`；结构化知识使用 `scripts/import_structured_food_knowledge.py`。导入和检索必须使用同一模型路径与 profile。