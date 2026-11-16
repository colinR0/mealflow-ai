# 食材字典隔离与助手展示、忌口修复

- 日期：2026-11-11
- 用户确认：本次需要填写开发记录。
- 范围：新增库存食材选择、助手菜谱/技能展示、快捷结果关闭、已登记食材限制与替代推荐。

## 问题与行为

1. 菜谱来源卡片原来只有复制原始步骤/技能按钮，现直接展示步骤和技能 Markdown，来源链接仍可复制。
2. 删除选择字典食材后持续出现的内部实现提示；选中后抑制延迟搜索和过期搜索结果，过期失败也不能清空新结果。
3. 食材搜索原本就是 PostgreSQL SQL 查询，并未调用向量检索；污染来自此前 HowToCook 导入的未审核食材片段。新增 `catalog_source`，字典仅展示 `preset`、`user`，`recipe_import` 保留菜谱关联但不参与字典建议。
4. 迁移依据 HowToCook 稳定 UUID 识别历史导入，避免按食材名称或菜谱关联误伤用户数据。保留用户自建条目；用户主动输入新食材后持久化，主动输入已有导入名称则将该条目提升为用户食材。导入器不再覆盖预置/用户条目的元数据。
5. 字典初始化使用维护好的 36 条示例食材，事务 advisory lock 防止并发首次请求重复写入；保留既有别名。
6. 快捷结果关闭控制移到顶部，正文单独滚动；请求代次使关闭前的旧成功/失败不能重新打开或覆盖新结果。
7. 虾、虾仁、shrimp 使用统一别名。明确请求已登记受限食材时，先读家庭档案，按 `allergy` 提示过敏、按 `dislike` 提示忌口；从已发布且来源 approved 的菜谱中选择食材与步骤完整、未命中全部家庭限制及当前条件的其他菜谱。先按同类食材（虾→鱼、贝、紫菜等）、再按烹饪方法词、食材数量和耗时选择，不声称任意菜谱相似。替代结果携带真实本轮证据。未授权 `log:`/`lot:` 家庭记录引用仍拒绝；其他无法核验的引用只移除标记，不再把内部错误当成整段拒绝。
8. 普通菜谱检索结果继续按当前限制过滤，全部命中时给出受限食材说明；结果不可用仍保留原错误语义。
9. 用户仍看到旧的“回答包含未读取的证据引用，已拒绝”，根因是 API/worker 于 21:05 启动、修复代码 22:38 才写入，worker 不会热重载。验证时已重启 API 与 worker，真实请求 `trace` 出现 `check_registered_food_request`，不再进入旧的整段拒绝路径。

## 文件

- `apps/api/app/db/models.py`、`apps/api/migrations/versions/p0b1c2d3e4f5_ingredient_catalog_sources.py`
- `apps/api/app/services/ingredient_catalog.py`、`apps/api/app/api/routes/v2.py`
- `apps/api/app/services/food_constraints.py`、`apps/api/app/agent/durable_graph.py`
- `scripts/import_howtocook_rag.py`
- `apps/web/src/pages/fridge/manual-add.vue`、`apps/web/src/pages/assistant/index.vue`
- `apps/api/tests/test_ingredient_catalog_visibility.py`、`apps/api/tests/test_durable_graph.py`
- `apps/web/tests/assistant-interactions.test.mjs`、`docs/README.md`

## 验证

- 后端 PostgreSQL 测试：`test_ingredient_catalog_visibility.py`、`test_durable_graph.py`、`test_inventory_lifecycle_api.py`、`test_phase2_retrieval_templates.py`、`test_phase2_http.py`，64 passed、4 skipped。覆盖用户自建持久化、导入片段隐藏、虾别名过滤、过敏/忌口区分、替代结果真实引用、同类替代排序、伪造引用移除和未授权记录拒绝。
- 真实开发库端到端：发送“我想吃虾了”和“我想吃虾，不要辣”后 run 状态均为 `succeeded`，`trace` 含 `check_registered_food_request`，返回 2 条真实菜谱引用，替代菜为清蒸鲈鱼、蒜蓉粉丝蒸扇贝；回答先说明“档案登记了忌口虾仁”，未把普通忌口写成过敏，正常的“不要辣”补充没有绕过检查。验证用临时会话已删除。
- 前端 Node：助手交互、库存重试、离线搜索，共 15 passed。
- `npm run typecheck`、`npm run build:app-android` 通过；产物为 `apps/web/dist/build/app`，不是签名 APK。
- 本次相关后端文件和导入脚本 Ruff 检查通过；未对原有 `models.py` 整文件 lint 问题做无关改写。
- 本地开发库迁移前 `alembic current` 为 `n9a0b1c2d3e4`、heads 为 `p0b1c2d3e4f5`，已执行 upgrade head。迁移后 36 preset、1008 recipe_import、1 user，未删除历史数据。
- 开发库实际 SQL 字典搜索 `cm 两端葱段`、`青椒 代替)`、`也可以是泡面面饼` 均无结果；“虾”返回预置虾仁。既有用户番茄与预置番茄同名但身份独立，保留原记录。
- `git diff --check` 通过。

## 已知边界

- 未完成 Android 真机点击、长内容滚动与 SSE 验收；编译通过不等于真机验收。
- 本次模型外的限制处理使用确定性测试；未调用真实付费模型。替代菜谱按已登记字段核验，不推断未登记成分或实际包装内容；同类分组是食材名称的粗粒度匹配，不保证口味或营养等价。
- 修改后端代码后必须重启 API 与 worker；本次已重启本地实例。若改由其他终端启动，请重新执行 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` 和 `python -m app.worker`，否则仍会运行旧逻辑。
- 导入片段仍保存在菜谱关联中，本次解决公共字典污染，没有重写全部菜谱原料清洗规则。
- 本次没有重跑全量后端套件；上一任务已知的时区/默认模型断言失败不在本次修复范围。
- 修改未提交、未推送，保留工作区其他任务的未提交修改。
