<script lang="ts">
import { defineComponent } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, DocumentCopy, Download, EditPen } from '@element-plus/icons-vue'

interface PracticeTask {
  id: string
  phase: string
  title: string
  goal: string
  files: string[]
  concepts: string[]
  starter: string
  checks: string[]
}

const DRAFT_STORAGE_KEY = 'mealflow:code-practice:drafts'
const DONE_STORAGE_KEY = 'mealflow:code-practice:completed'

const tasks: PracticeTask[] = [
  {
    id: 'fastapi-route',
    phase: '第 1 关 · 请求与路由',
    title: '写一个可在 Swagger 验证的接口',
    goal: '理解浏览器请求如何进入 FastAPI 函数，并返回 JSON。',
    files: ['apps/api/app/main.py'],
    concepts: ['@app.get', '路径', 'JSON 返回值', 'Swagger'],
    starter: `from datetime import datetime\n\n@app.get('/api/v1/learning/hello')\ndef learning_hello():\n  return {\n    'message': '我正在学习 MealFlow',\n    'current_time': datetime.now().astimezone().isoformat(),\n  }`,
    checks: ['重启或确认 Uvicorn 已热重载', '在 /docs 找到接口', 'Try it out 后看到 JSON'],
  },
  {
    id: 'request-schema',
    phase: '第 2 关 · 请求校验',
    title: '为一个请求定义 Pydantic Schema',
    goal: '理解为什么后端不能直接相信浏览器传来的 JSON。',
    files: ['apps/api/app/schemas/chat.py', 'apps/api/app/api/routes/chat.py'],
    concepts: ['BaseModel', 'Field', 'POST 请求体', '类型校验'],
    starter: `from pydantic import BaseModel, Field\n\nclass LearningRequest(BaseModel):\n  topic: str = Field(min_length=1, max_length=80)\n  minutes: int = Field(ge=10, le=120)`,
    checks: [
      '缺少 topic 时应返回 422',
      'minutes 传字符串时观察校验结果',
      '写下 Schema 与数据库模型的区别',
    ],
  },
  {
    id: 'service-boundary',
    phase: '第 3 关 · 业务服务',
    title: '把业务逻辑放进 Service',
    goal: '路由只收请求和回响应；计算、查询和规则放在服务层。',
    files: ['apps/api/app/services/inventory_service.py', 'apps/api/app/api/routes/inventory.py'],
    concepts: ['分层', '输入', '输出', '可测试性'],
    starter: `def summarize_learning_progress(completed: int, total: int) -> dict[str, int]:\n  if total <= 0:\n    raise ValueError('total must be positive')\n\n  return {\n    'completed': completed,\n    'total': total,\n    'remaining': max(0, total - completed),\n  }`,
    checks: ['函数不依赖 FastAPI Request', '正常数据和异常数据都可验证', '为函数写 pytest'],
  },
  {
    id: 'function-calling',
    phase: '第 4 关 · Agent 工具',
    title: '设计一个只读 Agent 工具',
    goal: '模型负责选择工具，后端负责执行与校验。',
    files: ['apps/api/app/agent/conversation.py', 'apps/api/tests/test_conversation_agent.py'],
    concepts: ['Function Calling', '工具定义', '白名单', '测试'],
    starter: `# 先用中文回答，再写代码：\n# 1. 工具叫什么名字？\n# 2. 输入参数是什么？\n# 3. 返回什么事实？\n# 4. 为什么它不能修改数据库？\n# 5. 至少要测试什么？`,
    checks: [
      '工具名称清晰且只读',
      '参数有 JSON Schema',
      '工具加入白名单',
      '测试包含成功和失败场景',
    ],
  },
  {
    id: 'frontend-api',
    phase: '第 5 关 · Vue 调后端',
    title: '写一个 TypeScript API 调用',
    goal: '理解 ref、async/await、Promise 和后端 JSON 的连接方式。',
    files: ['apps/web/src/api/client.ts', 'apps/web/src/api/chat.ts'],
    concepts: ['interface', 'Promise', 'await', 'apiRequest'],
    starter: `interface LearningResponse {\n  message: string\n  status: string\n}\n\nexport function fetchLearningStatus(): Promise<LearningResponse> {\n  return apiRequest('/learning/status')\n}`,
    checks: ['接口路径不重复写 /api/v1', '返回类型与后端 JSON 对应', '调用处使用 await 并处理错误'],
  },
  {
    id: 'regression-test',
    phase: '第 6 关 · 测试与提交',
    title: '给一次改动补回归测试',
    goal: '不是“页面能点就行”，而是能证明改动没有破坏既有规则。',
    files: ['apps/api/tests/test_conversation_agent.py', 'apps/api/tests/test_menu_composer.py'],
    concepts: ['pytest', 'Arrange-Act-Assert', 'git diff', 'commit'],
    starter: `def test_learning_feature_returns_expected_result():\n  # Arrange：准备数据\n  # Act：调用被测函数\n  # Assert：验证可观察结果\n  assert True`,
    checks: [
      '测试名称描述业务行为',
      '断言结果而不是实现细节',
      '运行相关 pytest',
      '提交前查看 git diff',
    ],
  },
]

export default defineComponent({
  name: 'CodePracticeView',
  components: { Check, DocumentCopy, Download, EditPen },
  data() {
    return {
      tasks,
      Check,
      DocumentCopy,
      Download,
      EditPen,
      activeTaskId: tasks[0].id,
      draft: tasks[0].starter,
      drafts: {} as Record<string, string>,
      completedTaskIds: [] as string[],
      savedAt: '',
    }
  },
  computed: {
    activeTask(): PracticeTask {
      return this.tasks.find((task) => task.id === this.activeTaskId) ?? this.tasks[0]
    },
    completedCount(): number {
      return this.completedTaskIds.length
    },
    progress(): number {
      return Math.round((this.completedCount / this.tasks.length) * 100)
    },
    isCompleted(): boolean {
      return this.completedTaskIds.includes(this.activeTask.id)
    },
  },
  mounted() {
    try {
      this.drafts = JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) ?? '{}')
      this.completedTaskIds = JSON.parse(localStorage.getItem(DONE_STORAGE_KEY) ?? '[]')
    } catch {
      this.drafts = {}
      this.completedTaskIds = []
    }
    this.draft = this.drafts[this.activeTaskId] ?? this.activeTask.starter
  },
  methods: {
    loadTask(task: PracticeTask) {
      this.saveDraft(false)
      this.activeTaskId = task.id
      this.draft = this.drafts[task.id] ?? task.starter
      this.savedAt = ''
    },
    saveDraft(showMessage = true) {
      this.drafts[this.activeTaskId] = this.draft
      localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(this.drafts))
      this.savedAt = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      if (showMessage) ElMessage.success('草稿已保存在当前浏览器')
    },
    resetDraft() {
      this.draft = this.activeTask.starter
      this.saveDraft(false)
      ElMessage.info('已恢复本关起始代码')
    },
    async copyDraft() {
      try {
        await navigator.clipboard.writeText(this.draft)
        ElMessage.success('代码已复制，可粘贴到项目文件中')
      } catch {
        ElMessage.error('复制失败，请手动复制代码')
      }
    },
    toggleCompleted() {
      const id = this.activeTask.id
      this.completedTaskIds = this.isCompleted
        ? this.completedTaskIds.filter((item) => item !== id)
        : [...this.completedTaskIds, id]
      localStorage.setItem(DONE_STORAGE_KEY, JSON.stringify(this.completedTaskIds))
    },
  },
})
</script>

<template>
  <section class="code-practice-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">MealFlow 学习工作台</p>
        <h2>代码练兵场</h2>
        <p>从真实业务出发写小段代码：先在这里想清楚，再复制到项目里运行和测试。</p>
      </div>
      <div class="progress-card">
        <strong>{{ completedCount }} / {{ tasks.length }}</strong>
        <span>已完成关卡</span>
        <el-progress :percentage="progress" :stroke-width="8" :show-text="false" />
      </div>
    </header>

    <div class="practice-layout">
      <aside class="task-list" aria-label="学习关卡">
        <button
          v-for="task in tasks"
          :key="task.id"
          class="task-button"
          :class="{ active: task.id === activeTask.id, done: completedTaskIds.includes(task.id) }"
          type="button"
          @click="loadTask(task)"
        >
          <el-icon><Check v-if="completedTaskIds.includes(task.id)" /><EditPen v-else /></el-icon>
          <span
            ><small>{{ task.phase }}</small
            ><strong>{{ task.title }}</strong></span
          >
        </button>
      </aside>

      <main class="practice-workbench">
        <section class="task-intro">
          <p class="eyebrow">{{ activeTask.phase }}</p>
          <h3>{{ activeTask.title }}</h3>
          <p>{{ activeTask.goal }}</p>
          <div class="chips">
            <span v-for="concept in activeTask.concepts" :key="concept">{{ concept }}</span>
          </div>
        </section>

        <div class="workbench-grid">
          <section class="code-panel">
            <div class="panel-title">
              <div>
                <strong>我的代码草稿</strong><small>只保存到此浏览器，不会自动修改项目文件。</small>
              </div>
              <span v-if="savedAt">已保存 {{ savedAt }}</span>
            </div>
            <textarea v-model="draft" spellcheck="false" aria-label="代码草稿" />
            <div class="code-actions">
              <el-button :icon="Download" @click="saveDraft">保存草稿</el-button>
              <el-button :icon="DocumentCopy" @click="copyDraft">复制到项目</el-button>
              <el-button text @click="resetDraft">恢复起始代码</el-button>
            </div>
          </section>

          <aside class="task-guide">
            <section>
              <h4>先看这些文件</h4>
              <code v-for="file in activeTask.files" :key="file">{{ file }}</code>
            </section>
            <section>
              <h4>完成标准</h4>
              <ol>
                <li v-for="check in activeTask.checks" :key="check">{{ check }}</li>
              </ol>
            </section>
            <el-button
              class="complete-button"
              :type="isCompleted ? 'success' : 'primary'"
              @click="toggleCompleted"
            >
              {{ isCompleted ? '已完成，取消标记' : '我已完成本关' }}
            </el-button>
          </aside>
        </div>

        <p class="learning-rule">
          <strong>练习规则：</strong>不要直接让 AI
          写完整答案。先写下输入、输出、校验和失败情况；卡住后再带着你的草稿来问。
        </p>
      </main>
    </div>
  </section>
</template>

<style scoped>
.code-practice-page {
  max-width: 1380px;
  margin: 0 auto;
  padding: 8px 0 36px;
  color: #213d35;
}
.page-heading {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 28px;
  margin: 14px 0 24px;
}
.eyebrow {
  margin: 0 0 7px;
  color: #548172;
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
h2,
h3,
h4,
p {
  margin-top: 0;
}
h2 {
  margin-bottom: 8px;
  font-size: 30px;
}
.page-heading > div > p:last-child {
  margin-bottom: 0;
  color: #658078;
}
.progress-card {
  width: 190px;
  padding: 15px 17px;
  border: 1px solid #dcebe4;
  border-radius: 15px;
  background: #f8fcfa;
}
.progress-card strong {
  display: block;
  font-size: 22px;
}
.progress-card span {
  display: block;
  margin: 2px 0 11px;
  color: #789188;
  font-size: 12px;
}
.practice-layout {
  display: grid;
  grid-template-columns: 285px minmax(0, 1fr);
  gap: 18px;
}
.task-list,
.practice-workbench {
  border: 1px solid #dcebe4;
  border-radius: 18px;
  background: #fff;
}
.task-list {
  display: grid;
  align-content: start;
  gap: 5px;
  padding: 10px;
}
.task-button {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 11px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: #416158;
  text-align: left;
  cursor: pointer;
}
.task-button:hover {
  background: #f1f8f4;
}
.task-button.active {
  background: #e8f5ee;
  color: #1d654b;
}
.task-button.done .el-icon {
  color: #3b9b6b;
}
.task-button span {
  display: grid;
  gap: 3px;
}
.task-button small {
  color: #82988f;
  font-size: 10px;
}
.task-button strong {
  font-size: 13px;
}
.practice-workbench {
  min-width: 0;
  padding: 25px;
}
.task-intro {
  padding-bottom: 19px;
  border-bottom: 1px solid #e8f0ec;
}
.task-intro h3 {
  margin-bottom: 8px;
  font-size: 23px;
}
.task-intro > p:not(.eyebrow) {
  margin-bottom: 13px;
  color: #607c72;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.chips span {
  padding: 4px 9px;
  border-radius: 999px;
  background: #f0f7f3;
  color: #46806a;
  font-size: 12px;
}
.workbench-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 265px;
  gap: 18px;
  margin-top: 20px;
}
.code-panel,
.task-guide section {
  border: 1px solid #e1ece6;
  border-radius: 14px;
  background: #fbfdfc;
}
.code-panel {
  overflow: hidden;
}
.panel-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid #e1ece6;
}
.panel-title div {
  display: grid;
  gap: 3px;
}
.panel-title strong {
  font-size: 14px;
}
.panel-title small,
.panel-title > span {
  color: #80958c;
  font-size: 11px;
}
textarea {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-height: 390px;
  padding: 17px;
  border: 0;
  outline: 0;
  resize: vertical;
  background: #172923;
  color: #d6f0df;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.65;
}
.code-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
}
.code-actions .el-button:last-child {
  margin-left: auto;
}
.task-guide {
  display: grid;
  align-content: start;
  gap: 13px;
}
.task-guide section {
  padding: 15px;
}
.task-guide h4 {
  margin-bottom: 10px;
  font-size: 13px;
}
.task-guide code {
  display: block;
  margin: 6px 0;
  color: #4e7568;
  font-size: 11px;
  overflow-wrap: anywhere;
}
.task-guide ol {
  margin: 0;
  padding-left: 18px;
  color: #607a70;
  font-size: 12px;
  line-height: 1.7;
}
.complete-button {
  width: 100%;
  height: 39px;
}
.learning-rule {
  margin: 19px 0 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: #fff9ed;
  color: #80633b;
  font-size: 12px;
  line-height: 1.6;
}
@media (max-width: 940px) {
  .page-heading,
  .practice-layout,
  .workbench-grid {
    grid-template-columns: 1fr;
  }
  .page-heading {
    align-items: start;
  }
  .progress-card {
    width: 100%;
    box-sizing: border-box;
  }
  .task-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .task-button {
    min-height: 66px;
  }
  .practice-workbench {
    padding: 18px;
  }
}
@media (max-width: 580px) {
  .task-list {
    grid-template-columns: 1fr;
  }
  .code-actions {
    flex-wrap: wrap;
  }
  .code-actions .el-button:last-child {
    margin-left: 0;
  }
}
</style>
