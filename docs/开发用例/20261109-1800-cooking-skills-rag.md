# 烹饪技能知识库与联网摘要兜底

- 日期：2026-11-09；用户授权实施并要求填写开发记录。
- 范围：通用知识 ORM/迁移、原生与 demo 检索、持久化 Agent 与模型协议、技能导入脚本、本地启动入口、助手来源展示及回归测试。
- 新迁移：`i4d5e6f7a8b9`，已在独立 `mealflow_rag` 执行；其他数据库启动前必须正常升级，不绕过 schema guard。存在非菜谱文档时 downgrade 明确拒绝，不删除技能数据。

## 行为

KnowledgeDocument 新增 document_type/title/source_path，recipe_id 可空。技能使用 cooking_skill 类型和 knowledge:文档ID:v版本引用；菜谱继续使用 recipe:菜谱ID:v版本。两种检索路径都按 document_id 去重；菜谱工具与配餐仅使用 recipe 类型。

Markdown 保留完整原文、标题层级检索文本及相对来源路径，整篇单块。导入支持只读预演，--apply 才写入；仅允许 development、mealflow_rag、native/fake/64/v1/v1，检查迁移与向量维度，复用现有菜谱导入器的 profile 检查和完整索引复用逻辑。HowToCook tips 整目录、子目录和单文件均以 tips 为相对根；其他数据集应保持固定导入根。变更递增文档版本，不自动删除缺失文件。

search_cooking_skills 优先本地；只有 no_result 触发异步 DuckDuckGo HTML 搜索。数据库或索引故障保留原错误。网络摘要使用稳定 URL 哈希引用，明确 search_snippet、来源 URL、检索时间，不抓网页全文、不自动入库。禁用、超时、HTTP 错误、挑战页、无法解析和真正空结果分别处理。

配置：KNOWLEDGE_WEB_SEARCH_ENABLED 默认 true，KNOWLEDGE_WEB_SEARCH_TIMEOUT_SECONDS 默认 6（最大 15）。前端显示技能/网络来源、摘要、可复制 URL 和技能原文。最终回答校验接受 knowledge/web 引用并拒绝漏标最近一次检索来源。

## 实际效果

- 导入 HowToCook tips 全部 19 篇（包括 learn、advanced、厨房准备/洗碗/决策吃什么）。原有菜谱 443 篇，合计 462 个活动完整块，fake 64 维。
- 首次 created=19、updated=0；再次 created=0、updated=0，复用 generation `73a8a22b-a587-4fc4-8ce3-0632e40bbd89`。
- 实际工具查询“焯水”返回《焯水》《去腥》《做菜专业术语》；“油温”返回《油温判断技巧及常见温度和单位换算表》；“食品安全”命中同名文档等。菜谱搜索仍返回 ok、8 条。
- “排骨冷水还是热水下锅”未命中本地，触发网搜；本机实网返回 WEB_SEARCH_FAILED，未获得真实网络证据。网络成功引用链通过注入搜索结果验证，不能宣称联网可用性验收通过。

## 验证

- 相关 5 个测试文件回归 59 passed、1 skipped；随后新增 Markdown 原文保留/幂等/版本递增测试，技能文件独立运行 15 passed。测试使用独立 plain/native 临时 PostgreSQL 测试库，无真实模型与网络调用。
- 覆盖 native/demo 文档隔离、多个 NULL recipe_id 不合并、无关查询无命中、只在 no_result 网搜、禁用/超时/HTTP 错误/挑战页/解析/空结果、完整图 knowledge/web 引用及漏引用拒绝。
- 修改 Python 文件 Ruff 通过，git diff --check 通过；前端 typecheck、Android 编译通过（dist/build/app，不是签名 APK），有既有 Alembic/Sass 弃用警告。

## 已知限制

fake embedding 仅用于流程验证。技能向量候选也要求关键词匹配，长句、同义词可能漏召回；工具提示要求提取技巧关键词。尚无证据充分性判断；本地非空但只覆盖部分问题时不会自动联网补全。真实 embedding 接入时需重新评估并调整关键词门槛。

网络只有搜索摘要，未实现网页正文读取与来源质量评估；当前环境实网失败。真实 LLM 调用、HTTP 会话及手机页面未验收。引用存在性校验不证明每句回答忠实，复杂多工具最终状态仍以最近一次检索为准。

## 使用

在仓库根运行 `scripts/start-rag-local.ps1 -Mode ImportSkills -Source 'D:\LLM\agent\meal\data\HowToCook-master\tips'` 可重复导入。只读预演使用 Conda Python 运行 `scripts/import_cooking_skills.py --source <目录或文件>`，不加 --apply。启动 API 与 worker 分别使用启动脚本 Api/Worker 模式，确保都连接 mealflow_rag。
