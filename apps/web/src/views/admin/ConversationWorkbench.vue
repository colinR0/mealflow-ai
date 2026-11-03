<script lang="ts">
import { defineComponent, nextTick, markRaw, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createProcurementItem,
  fetchProcurement,
  generateProcurement,
} from '../../composables/procurement'
import { useAgentHandoffStore } from '../../stores/agentHandoff'
import { ArrowDown, Close, Delete, Search, User } from '@element-plus/icons-vue'
import { conversationStarted, greeting as timeGreeting } from '../../composables/greeting'
import {
  createHouseholdEvent,
  deleteFoodPreference,
  deleteChatSessions,
  deleteChatSession,
  fetchChatSession,
  fetchChatSessionCount,
  fetchChatSessions,
  fetchChatModels,
  fetchHousehold,
  fetchRecipeDetail,
  streamChat,
  type ChatSessionSummary,
  type ChatModel,
  type Household,
  type RecipeDetail,
} from '../../api/chat'
import type { AgentRun } from '../../types/agent'
interface StatusStep {
  step: string
  /** 后端按本次工具调用动态生成的步骤名,优先于本地标签表显示 */
  label?: string
  state: 'running' | 'done' | 'error'
  detail: string
}
interface CandidateItem {
  recipe_id: string
  name: string
  ingredients_summary: string
  conflict_level: 'forbidden' | 'warn' | null
  conflict_summary: string | null
  expiring: boolean
  expiring_used: string[]
  used_fridge: string[]
  missing: string[]
}
interface WebRecipeItem {
  title: string
  url: string
  ingredients: string
  rating: string | null
  cooked_count: string | null
  steps_brief: string[]
}
interface ToolCallTrace {
  tool_name: string
  status: 'success' | 'error' | string
  duration_ms: number
  input_summary: string
  output_summary: string
  error_code?: string | null
}
interface ProcurementSuggestion {
  ingredient: string
  quantity: string
  unit: string
  recipe_id: string
  recipe_name: string
  source_id: string
  reason: string
  added?: boolean
}
interface MenuIngredient {
  name: string
  display: string
}
interface MenuItem {
  recipe_id: string
  name: string
  role?: string
  role_label?: string
  target_servings?: number
  duration_minutes: number
  state: 'ready' | 'purchase_optional' | 'restricted'
  scaled_ingredients?: MenuIngredient[]
  used_inventory: string[]
  pantry_staples: string[]
  missing_main_ingredients: string[]
  restricted_ingredients: string[]
  source?: 'local' | 'model_generated'
  warnings?: string[]
}
interface PreferenceUpdateEvent {
  status: 'saved' | 'skipped'
  id?: string
  target_name?: string
  message: string
  undone?: boolean
}
interface GeneratedRecipeEvent {
  recipe_id: string
  name: string
  source: 'model_generated'
}
interface MenuStaple {
  type: string
  name: string
  suggested_quantity?: string
  cooked_quantity?: string
  recipe_id?: string
  scaled_ingredients?: MenuIngredient[]
}
interface MenuRecommendation {
  diners?: number
  dish_count: number
  meal_structure?: string
  staple: MenuStaple | string | null
  items: MenuItem[]
  balance_summary?: string
  portion_note?: string
  relaxations?: string[]
  ready_count: number
  purchase_optional_count: number
  restricted_count: number
  estimated_parallel_minutes: number
  reason: string
}
interface ChatBubble {
  id: string
  role: 'user' | 'assistant'
  modelId?: string
  modelSwitchLabel?: string
  content: string
  thinking: string
  thinkingDone: boolean
  steps: StatusStep[]
  plan: AgentRun | null
  selectedStrategy: string
  questions: string[]
  streaming: boolean
  error: string
  stopped?: boolean
  searchQuery?: string
  candidates?: CandidateItem[]
  candidatesQuery?: string
  webQuery?: string
  webRecipes?: WebRecipeItem[]
  toolCalls?: ToolCallTrace[]
  toolTraceOpen?: boolean
  procurementSuggestions?: ProcurementSuggestion[]
  menuRecommendation?: MenuRecommendation
  toolBudget?: { used: number; limit: number }
  thinkingOpen?: boolean
  preferenceUpdates?: PreferenceUpdateEvent[]
  generatedRecipes?: GeneratedRecipeEvent[]
}
const STEP_LABELS: Record<string, string> = {
  session: '会话就绪',
  parse_request: '理解需求',
  get_meal_service_context: '确认餐次',
  list_inventory: '读取冰箱',
  get_member_constraints: '检查用餐限制',
  get_inventory_summary: '读取冰箱库存',
  get_preference_summary: '读取家庭偏好',
  search_recipes: '检索本地菜谱',
  get_recipe_detail: '读取菜谱做法',
  check_recipe_availability: '核对食材可用性',
  recommend_menu: '生成本餐建议',
  agent_decide: '分析需求',
  route_request: '判断处理方式',
  search_recipe_candidates: '召回菜谱',
  filter_hard_constraints: '过滤硬约束',
  score_menu_options: '方案评分',
  scale_recipe_quantities: '按人数换算',
  estimate_nutrition_and_cost: '估算热量成本',
  build_procurement_list: '生成采购缺口',
}
const EVENT_TYPES = ['例假', '感冒', '肠胃不适', '康复期', '最近在健身', '减脂', '其他']
const CHAT_SCROLL_KEY = 'mealflow_agent_chat_scroll_top_v1'
const CHAT_SESSION_KEY = 'mealflow_agent_chat_session_id_v1'
const CHAT_MODEL_KEY = 'mealflow_agent_model_id_v1'
const PROTOCOL_LEAK = /<\|DSML\||<\uFF5CDSML\uFF5C|invoke\s+name\s*=|tool_calls/i

export default defineComponent({
  name: 'ConversationWorkbench',
  components: { ArrowDown, Close, Delete, Search, User },
  data() {
    return {
      timeGreeting,
      messages: [] as ChatBubble[],
      input: '',
      sending: false,
      currentAbort: null as AbortController | null,
      sessionId: null as string | null,
      recipeCache: {} as Record<string, RecipeDetail>,
      expandedDish: null as string | null,
      household: null as Household | null,
      selectedDinerIds: [] as string[],
      dinerPickerOpen: false,
      knownMemberIds: new Set<string>(),
      savedChatScrollTop: 0,
      chatModels: [] as ChatModel[],
      selectedModelId: '',
      modelMenuOpen: false,
      historyDrawerOpen: false,
      historySearch: '',
      sessions: [] as ChatSessionSummary[],
      totalHistoryCount: 0,
      sessionsLoading: false,
      restoring: false,
      deletingSessionId: null as string | null,
      clearingHistory: false,
      agentHandoff: useAgentHandoffStore(),
      scrollFrame: null as number | null,
      followLatest: true,
      ArrowDown: markRaw(ArrowDown),
      Close: markRaw(Close),
      Delete: markRaw(Delete),
      Search: markRaw(Search),
      User: markRaw(User),
    }
  },
  computed: {
    selectedModel() {
      return this.chatModels.find((item) => item.id === this.selectedModelId) ?? null
    },
    activeSessionTitle() {
      return this.sessions.find((item) => item.session_id === this.sessionId)?.title ?? '新对话'
    },
    historyCount() {
      return this.totalHistoryCount
    },
    sessionOperationBusy() {
      return (
        this.sending || this.restoring || this.clearingHistory || Boolean(this.deletingSessionId)
      )
    },
    filteredSessions() {
      const keyword = this.historySearch.trim().toLocaleLowerCase('zh-CN')
      if (!keyword) return this.sessions
      return this.sessions.filter((item) => item.title.toLocaleLowerCase('zh-CN').includes(keyword))
    },
    historyGroups() {
      const today = new Date()
      today.setHours(0, 0, 0, 0)
      const recentBoundary = new Date(today)
      recentBoundary.setDate(recentBoundary.getDate() - 6)
      const groups = [
        { key: 'today', label: '今天', items: [] as ChatSessionSummary[] },
        { key: 'recent', label: '近 7 天', items: [] as ChatSessionSummary[] },
        { key: 'earlier', label: '更早', items: [] as ChatSessionSummary[] },
      ]
      for (const item of this.filteredSessions) {
        const updatedAt = new Date(item.updated_at)
        if (updatedAt >= today) groups[0].items.push(item)
        else if (updatedAt >= recentBoundary) groups[1].items.push(item)
        else groups[2].items.push(item)
      }
      return groups.filter((group) => group.items.length)
    },
    selectedDiners() {
      return (this.household?.members ?? []).filter((member) =>
        this.selectedDinerIds.includes(member.user_id),
      )
    },
  },
  methods: {
    seq(): string {
      return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    },
    async loadChatModels() {
      try {
        const models = await fetchChatModels()
        this.chatModels = models
        const saved = localStorage.getItem(CHAT_MODEL_KEY)
        const preferred = models.find((item) => item.id === saved && item.enabled)
        const fallback =
          models.find((item) => item.default && item.enabled) ?? models.find((item) => item.enabled)
        this.selectedModelId = preferred?.id ?? fallback?.id ?? ''
      } catch {
        this.chatModels = []
        this.selectedModelId = ''
      }
    },
    chooseModel(model: ChatModel) {
      if (!model.enabled || this.sending) return
      this.selectedModelId = model.id
      localStorage.setItem(CHAT_MODEL_KEY, model.id)
      this.modelMenuOpen = false
    },
    modelHint(model: ChatModel): string {
      if (!model.enabled) return '尚未配置连接'
      if (model.use_case) return model.use_case
      if (model.is_free) return '官方免费模型 · 支持 Agent 工具调用'
      if (model.provider === 'aitechflux') return 'AITechFlux · 对话与工具调用'
      return '家庭厨房 · 日常推荐'
    },
    dinerTriggerLabel(): string {
      return this.selectedDiners.length
        ? this.selectedDiners.map((member) => member.display_name).join('、')
        : '选择用餐人员'
    },
    relativeTime(iso: string): string {
      const delta = Date.now() - new Date(iso).getTime()
      if (Number.isNaN(delta)) return ''
      const minutes = Math.floor(delta / 60000)
      if (minutes < 1) return '刚刚'
      if (minutes < 60) return `${minutes} 分钟前`
      const hours = Math.floor(minutes / 60)
      if (hours < 24) return `${hours} 小时前`
      return `${Math.floor(hours / 24)} 天前`
    },
    async loadSessions() {
      this.sessionsLoading = true
      try {
        const [items, total] = await Promise.all([
          fetchChatSessions(),
          fetchChatSessionCount('home-workspace', this.sessionId),
        ])
        this.sessions = items
        this.totalHistoryCount = total.count
      } catch {
        // 历史加载失败不打断当前对话，抽屉保留上一次成功结果。
      } finally {
        this.sessionsLoading = false
      }
    },
    openHistoryDrawer() {
      this.historyDrawerOpen = true
      this.historySearch = ''
      void this.loadSessions()
    },
    startNewSession() {
      if (this.sending || this.restoring) return
      this.currentAbort?.abort()
      this.sessionId = null
      sessionStorage.removeItem(CHAT_SESSION_KEY)
      this.messages = []
      this.expandedDish = null
      this.followLatest = true
      this.historyDrawerOpen = false
      void this.loadSessions()
    },
    async removeSession(item: ChatSessionSummary) {
      if (this.sessionOperationBusy) return
      try {
        await ElMessageBox.confirm(`删除「${item.title}」？删除后不可恢复。`, '删除对话', {
          confirmButtonText: '删除',
          cancelButtonText: '取消',
          type: 'warning',
          customClass: 'history-confirm-box',
          modalClass: 'history-confirm-overlay',
        })
      } catch {
        return // 用户取消
      }
      this.deletingSessionId = item.session_id
      try {
        await deleteChatSession(item.session_id)
        const deletedCurrent = this.sessionId === item.session_id
        this.sessions = this.sessions.filter((entry) => entry.session_id !== item.session_id)
        if (deletedCurrent) {
          this.currentAbort?.abort()
          this.sessionId = null
          sessionStorage.removeItem(CHAT_SESSION_KEY)
          this.messages = []
          this.expandedDish = null
        } else {
          this.totalHistoryCount = Math.max(0, this.totalHistoryCount - 1)
        }
        ElMessage.success('对话已删除')
      } catch {
        ElMessage.error('删除失败，请重试')
      } finally {
        this.deletingSessionId = null
      }
    },
    async clearHistory() {
      if (this.sessionOperationBusy || this.historyCount === 0) return
      const count = this.historyCount
      try {
        await ElMessageBox.confirm(
          `将永久删除当前对话之外的 ${count} 条历史记录，当前对话会保留。`,
          '清空历史对话',
          {
            confirmButtonText: '清空历史',
            cancelButtonText: '取消',
            type: 'warning',
            customClass: 'history-confirm-box',
            modalClass: 'history-confirm-overlay',
          },
        )
      } catch {
        return
      }
      this.clearingHistory = true
      try {
        const result = await deleteChatSessions('home-workspace', this.sessionId)
        this.sessions = this.sessions.filter((item) => item.session_id === this.sessionId)
        this.totalHistoryCount = 0
        this.historySearch = ''
        ElMessage.success(
          result.deleted_sessions
            ? `已清空 ${result.deleted_sessions} 条历史对话`
            : '没有需要清理的历史对话',
        )
      } catch {
        ElMessage.error('清空失败，请重试')
      } finally {
        this.clearingHistory = false
      }
    },
    async restoreSession(id: string) {
      if (this.sending || this.restoring || id === this.sessionId) {
        this.historyDrawerOpen = false
        return
      }
      this.restoring = true
      this.historyDrawerOpen = false
      try {
        const view = await fetchChatSession(id)
        this.currentAbort?.abort()
        this.sessionId = view.session_id
        sessionStorage.setItem(CHAT_SESSION_KEY, view.session_id)
        this.expandedDish = null
        this.followLatest = true
        this.messages = view.messages.map((message) =>
          reactive({
            id: message.id,
            role: message.role === 'user' ? ('user' as const) : ('assistant' as const),
            content:
              message.role === 'assistant'
                ? this.publicAssistantText(message.content)
                : message.content,
            thinking: message.thinking_text ?? '',
            thinkingDone: true,
            thinkingOpen: false,
            steps: [],
            plan: null,
            selectedStrategy: '',
            questions: [],
            streaming: false,
            error: '',
          }),
        )
        void this.loadSessions()
        void nextTick(() => {
          const list = this.$refs.listEl as HTMLElement | undefined
          if (list) list.scrollTop = list.scrollHeight
        })
      } catch {
        ElMessage.error('会话恢复失败，请重试')
      } finally {
        this.restoring = false
      }
    },
    async loadHousehold() {
      try {
        this.household = await fetchHousehold()
        const currentIds = this.household.members.map((member) => member.user_id)
        this.selectedDinerIds = this.selectedDinerIds.filter((id) => currentIds.includes(id))
        for (const member of this.household.members) {
          if (!this.knownMemberIds.has(member.user_id) && member.residency === 'resident') {
            this.selectedDinerIds.push(member.user_id)
          }
          this.knownMemberIds.add(member.user_id)
        }
      } catch {
        // 家庭档案加载失败不阻断对话
      }
    },
    rememberChatScroll() {
      const list = this.$refs.listEl as HTMLElement | undefined
      if (!list) return
      this.savedChatScrollTop = list.scrollTop
      sessionStorage.setItem(CHAT_SCROLL_KEY, String(list.scrollTop))
    },
    restoreChatScroll() {
      void nextTick(() => {
        const list = this.$refs.listEl as HTMLElement | undefined
        if (!list || !this.messages.length) return
        const stored = Number(sessionStorage.getItem(CHAT_SCROLL_KEY) ?? this.savedChatScrollTop)
        if (!Number.isFinite(stored)) return
        list.scrollTop = stored
        this.onListScroll()
      })
    },
    onListScroll() {
      const list = this.$refs.listEl as HTMLElement | undefined
      if (list) this.followLatest = list.scrollHeight - list.scrollTop - list.clientHeight < 96
    },
    scrollBottom() {
      if (this.scrollFrame !== null || !this.followLatest) return
      this.scrollFrame = requestAnimationFrame(() => {
        this.scrollFrame = null
        void nextTick(() => {
          const list = this.$refs.listEl as HTMLElement | undefined
          if (list && this.followLatest) list.scrollTop = list.scrollHeight
        })
      })
    },
    stepLabel(stepItem: StatusStep): string {
      return stepItem.label || STEP_LABELS[stepItem.step] || stepItem.step
    },
    strategySummary(strategy: string): string {
      const summaries: Record<string, string> = {
        expiring_first: '优先消耗临期食材',
        original_first: '尊重菜谱原版做法与标准配比',
        balanced_first: '综合评估临期、口味、时间与预算',
        dislike_first: '优先避开长期忌口，过敏原始终为红线',
      }
      return summaries[strategy] ?? '按当前家庭条件综合安排'
    },
    publicAssistantText(text: string): string {
      if (!text || PROTOCOL_LEAK.test(text)) return ''
      return text
    },
    renderMarkdown(text: string): string {
      const safe = this.publicAssistantText(text)
      if (!safe) return ''
      const escaped = this.polishClarification(safe)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
      return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br/>')
    },
    stapleOf(menu: MenuRecommendation): MenuStaple | null {
      if (!menu.staple) return null
      if (typeof menu.staple === 'string') return { type: menu.staple, name: menu.staple }
      return menu.staple
    },
    menuSections(menu: MenuRecommendation): Array<{ label: string; items: MenuItem[] }> {
      const order = ['荤菜', '荤素', '素菜', '汤', '菜品']
      const groups = new Map<string, MenuItem[]>(order.map((label) => [label, []]))
      for (const item of menu.items) {
        const label = item.role_label && groups.has(item.role_label) ? item.role_label : '菜品'
        groups.get(label)?.push(item)
      }
      return order
        .filter((label) => (groups.get(label) || []).length)
        .map((label) => ({ label, items: groups.get(label) || [] }))
    },
    ingredientLine(item: { scaled_ingredients?: MenuIngredient[] }): string {
      return (item.scaled_ingredients || []).map((row) => `${row.name} ${row.display}`).join('、')
    },
    money(cents: number | null | undefined): string {
      if (cents == null) return '—'
      return `¥${(cents / 100).toFixed(2)}`
    },
    budgetLine(plan: AgentRun): string {
      const budget = plan.constraints.budget_limit_cents
      if (!budget) return ''
      const diff = budget - (plan.options[0]?.estimated_cost_cents ?? 0)
      return diff >= 0 ? `预算还剩 ${this.money(diff)}` : `超出预算 ${this.money(-diff)}`
    },
    planBudgetText(plan: AgentRun | null): string {
      if (!plan) return ''
      return this.budgetLine(plan)
    },
    dishWarning(plan: AgentRun, dish: string): string {
      for (const option of plan.options) {
        for (const warning of option.warnings ?? []) {
          if (warning.includes(`:${dish}——`)) return warning
        }
      }
      return ''
    },
    warningLevel(warning: string): 'forbidden' | 'warn' {
      return warning.startsWith('⛔') ? 'forbidden' : 'warn'
    },
    polishClarification(text: string): string {
      const prefix =
        '好,先确认两件事:这餐几个人吃?有预算上限的话我一并算进成本和采购缺口里。全家当前的饮食限制我记着呢:'
      let polished = text.replace(
        prefix,
        '好，我们来安排这顿饭。请在输入框下方确认用餐人员；有预算上限，也可以直接告诉我，我会一并计算食材用量、预计费用和需要采购的食材。\n\n已读取的家庭饮食提醒：\n',
      )
      for (const member of this.household?.members ?? []) {
        for (const restriction of member.restrictions) {
          polished = polished.replaceAll(restriction.ingredient_key, restriction.ingredient_name)
        }
      }
      if (text.includes(prefix))
        polished = polished.replaceAll('；', '\n').replaceAll('(', '（').replaceAll(')', '）')
      return polished
    },
    activeStep(bubble: ChatBubble): StatusStep | undefined {
      if (bubble.content) return undefined
      return [...bubble.steps].reverse().find((step) => step.state === 'running')
    },
    traceSummary(bubble: ChatBubble): string {
      const traces = (bubble.toolCalls ?? []).filter((trace) => trace.status !== 'running')
      const successful = [
        ...new Set(
          traces
            .filter((trace) => trace.status === 'success')
            .map((trace) => this.stepLabel({ step: trace.tool_name, state: 'done', detail: '' })),
        ),
      ]
      const failed = traces.filter((trace) => trace.status === 'error').length
      const base = successful.length ? `已读取 ${successful.join('、')}` : '本轮未读取额外信息'
      const budget = bubble.toolBudget
        ? ` · 已用 ${bubble.toolBudget.used} / ${bubble.toolBudget.limit} 次`
        : ''
      return failed ? `${base} · ${failed} 项未完成${budget}` : `${base}${budget}`
    },
    traceHasIssue(bubble: ChatBubble): boolean {
      return (bubble.toolCalls ?? []).some((trace) => trace.status === 'error')
    },
    async toggleRecipe(dish: string, plan: AgentRun | null) {
      if (this.expandedDish === dish) {
        this.expandedDish = null
        return
      }
      this.expandedDish = dish
      if (this.recipeCache[dish]) return
      const recipeId = plan?.options
        .flatMap((option) => option.items)
        .find((item) => item.name === dish)?.recipe_id
      if (!recipeId) return
      const detail = await fetchRecipeDetail(
        String(recipeId),
        plan?.constraints.expected_diners ?? undefined,
      )
      this.recipeCache = { ...this.recipeCache, [dish]: detail }
    },
    async openCandidate(item: CandidateItem) {
      if (this.expandedDish === item.name) {
        this.expandedDish = null
        return
      }
      this.expandedDish = item.name
      if (this.recipeCache[item.name]) return
      const detail = await fetchRecipeDetail(
        String(item.recipe_id),
        this.selectedDiners.length || undefined,
      )
      this.recipeCache = { ...this.recipeCache, [item.name]: detail }
    },
    fridgeUsageText(item: CandidateItem): string {
      if (!item.used_fridge.length) return ''
      return item.used_fridge
        .map((name) => (item.expiring_used?.includes(name) ? `${name}(临期)` : name))
        .join('、')
    },
    async confirmPlan(bubble: ChatBubble) {
      if (this.sending) return
      const option = bubble.plan?.options.find((item) => item.strategy === bubble.selectedStrategy)
      if (!option || !bubble.plan) return
      if (option?.procurement_list?.length) {
        try {
          const added = await generateProcurement(
            option.procurement_list,
            `${bubble.plan.id}:${option.strategy}`,
          )
          ElMessage.success(
            added ? `已将 ${added} 项食材加入采购清单` : '这套方案的采购项已在清单中',
          )
        } catch {
          ElMessage.error('采购清单保存失败，请检查浏览器存储空间')
          return
        }
      } else {
        ElMessage.success('现有食材已足够，无需新增采购项')
      }
      this.ask(`确认${option.title}，就做这套`)
    },
    ask(text: string) {
      if (this.sending) return
      void this.send(text)
    },
    async send(preset?: string) {
      const displayMessage = (preset ?? this.input).trim()
      if (!displayMessage || this.sending) return
      if (this.household && !this.selectedDiners.length) {
        ElMessage.info('请先选择至少一位用餐人员')
        this.dinerPickerOpen = true
        return
      }
      const dinerContext = this.selectedDiners.length
        ? `本次用餐${this.selectedDiners.length}人，人员：${this.selectedDiners.map((member) => member.display_name).join('、')}`
        : '本次暂未选择用餐人员'
      const requestMessage = `${displayMessage}\n\n[${dinerContext}]`.slice(0, 2000)
      this.dinerPickerOpen = false
      this.followLatest = true
      this.sending = true
      this.input = ''
      const modelId = this.selectedModelId || undefined
      const previousModelId = [...this.messages]
        .reverse()
        .find((message) => message.modelId)?.modelId
      const modelSwitchLabel =
        previousModelId && modelId && previousModelId !== modelId
          ? this.selectedModel?.label
          : undefined
      this.messages.push({
        id: this.seq(),
        role: 'user',
        content: displayMessage,
        thinking: '',
        thinkingDone: false,
        steps: [],
        plan: null,
        selectedStrategy: '',
        questions: [],
        streaming: false,
        error: '',
      })
      // 必须用 reactive 引用:普通对象在 push 后与响应式代理脱节,流式更新不会触发渲染
      const assistant = reactive<ChatBubble>({
        id: this.seq(),
        role: 'assistant',
        modelId,
        modelSwitchLabel,
        content: '',
        thinking: '',
        thinkingDone: false,
        steps: [],
        plan: null,
        selectedStrategy: '',
        questions: [],
        streaming: true,
        error: '',
      })
      this.messages.push(assistant)
      this.scrollBottom()
      let thinkingIdleTimer: ReturnType<typeof setTimeout> | undefined

      // 中断控制器:发送后可主动停止流式生成(后端 SSE 随断开自动取消)
      const controller = new AbortController()
      this.currentAbort = controller

      try {
        for await (const event of streamChat(
          {
            session_id: this.sessionId,
            diners_user_ids: this.selectedDinerIds,
            model_id: modelId,
            message: requestMessage,
          },
          controller.signal,
        )) {
          if (event.event === 'thinking') {
            assistant.thinking += String(event.data.delta ?? '')
            assistant.thinkingDone = false
            assistant.thinkingOpen = true // 流式期间自动展开,实时看思维链
            clearTimeout(thinkingIdleTimer)
            thinkingIdleTimer = setTimeout(() => {
              assistant.thinkingDone = true
            }, 700)
          } else if (event.event === 'status') {
            const step = String(event.data.step ?? '')
            const label = String(event.data.label ?? '')
            const rawState = String(event.data.state ?? '')
            const state: StatusStep['state'] =
              rawState === 'running' ? 'running' : rawState === 'error' ? 'error' : 'done'
            const detail = String(event.data.detail ?? '')
            if (state === 'running') {
              assistant.steps.push({ step, label, state, detail: '' })
            } else {
              const reverseIdx = [...assistant.steps]
                .reverse()
                .findIndex((item) => item.step === step && item.state === 'running')
              if (reverseIdx >= 0) {
                const real = assistant.steps.length - 1 - reverseIdx
                assistant.steps[real] = {
                  step,
                  label: label || assistant.steps[real].label,
                  state,
                  detail,
                }
              } else {
                assistant.steps.push({ step, label, state, detail })
              }
            }
          } else if (event.event === 'preference_updated') {
            const update = event.data as unknown as PreferenceUpdateEvent
            assistant.preferenceUpdates ??= []
            assistant.preferenceUpdates.push(update)
            if (update.status === 'saved') ElMessage.success(update.message)
            else ElMessage.warning(update.message)
          } else if (event.event === 'recipe_generated') {
            const recipe = event.data as unknown as GeneratedRecipeEvent
            assistant.generatedRecipes ??= []
            if (!assistant.generatedRecipes.some((item) => item.recipe_id === recipe.recipe_id)) {
              assistant.generatedRecipes.push(recipe)
            }
          } else if (event.event === 'text_reset') {
            assistant.content = ''
          } else if (event.event === 'text') {
            assistant.content += String(event.data.delta ?? '')
            if (PROTOCOL_LEAK.test(assistant.content)) assistant.content = ''
            assistant.thinkingDone = true
          } else if (event.event === 'tool_call') {
            assistant.toolCalls ??= []
            const trace = event.data as unknown as ToolCallTrace
            const running = assistant.toolCalls.findIndex(
              (item) => item.tool_name === trace.tool_name && item.status === 'running',
            )
            if (running >= 0 && trace.status !== 'running') assistant.toolCalls[running] = trace
            else assistant.toolCalls.push(trace)
          } else if (event.event === 'procurement_suggestions') {
            const unique = new Map<string, ProcurementSuggestion>()
            for (const item of (event.data.items as ProcurementSuggestion[]) ?? []) {
              const key = `${item.source_id ?? ''}:${item.ingredient}`
              if (!unique.has(key)) unique.set(key, item)
            }
            assistant.procurementSuggestions = [...unique.values()]
          } else if (event.event === 'menu_recommendation') {
            assistant.menuRecommendation = event.data as unknown as MenuRecommendation
          } else if (event.event === 'plan') {
            assistant.thinkingDone = true
            assistant.plan = event.data as unknown as AgentRun
            assistant.selectedStrategy = assistant.plan.options[0]?.strategy ?? ''
          } else if (event.event === 'candidates') {
            // 食材模糊查询:候选菜列表(规则召回,不含 run),点击展开做法详情
            assistant.thinkingDone = true
            assistant.candidatesQuery = String(event.data.query ?? '')
            assistant.candidates = (event.data.items as CandidateItem[]) ?? []
          } else if (event.event === 'web_recipes') {
            // 本地库未命中时的实时搜索结果(下厨房,仅食材清单与步骤梗概)
            assistant.thinkingDone = true
            assistant.webQuery = String(event.data.query ?? '')
            assistant.webRecipes = (event.data.items as WebRecipeItem[]) ?? []
          } else if (event.event === 'questions') {
            assistant.questions = (event.data.items as string[]) ?? []
          } else if (event.event === 'error') {
            assistant.error = String(event.data.message ?? 'Agent 运行失败')
          } else if (event.event === 'done') {
            assistant.streaming = false
            assistant.thinkingDone = true
            // 菜单列表的思考过程保持展开(决策透明);其余分支收起,答案为主
            assistant.thinkingOpen = Boolean(assistant.candidates?.length)
            this.sessionId = (event.data.session_id as string) ?? this.sessionId
            if (this.sessionId) sessionStorage.setItem(CHAT_SESSION_KEY, this.sessionId)
            void this.loadSessions()
            assistant.searchQuery =
              typeof event.data.search_query === 'string' ? event.data.search_query : undefined
            assistant.toolBudget = event.data.tool_budget as
              | { used: number; limit: number }
              | undefined
          }
          this.scrollBottom()
        }
      } catch (reason) {
        // 主动停止:静默收尾并标注,不当错误弹给用户
        if (reason instanceof DOMException && reason.name === 'AbortError') {
          assistant.stopped = true
        } else {
          assistant.error = reason instanceof Error ? reason.message : '连接中断'
        }
      } finally {
        this.currentAbort = null
        clearTimeout(thinkingIdleTimer)
        assistant.streaming = false
        // 收尾兜底:中断/出错时把仍"进行中"的步骤标记完成,避免 mini-spinner 永转
        assistant.steps.forEach((item) => {
          if (item.state === 'running') item.state = 'done'
        })
        this.sending = false
        this.scrollBottom()
      }
    },
    async addSuggestion(item: ProcurementSuggestion) {
      try {
        const existing = await fetchProcurement()
        if (
          !existing.some(
            (row) => row.sourceId === item.source_id && row.ingredient === item.ingredient,
          )
        ) {
          await createProcurementItem({
            ingredient: item.ingredient,
            quantity: item.quantity,
            unit: item.unit,
            checked: false,
            source: 'agent',
            sourceId: item.source_id,
          })
        }
        item.added = true
        ElMessage.success(`${item.ingredient} 已加入采购清单`)
      } catch {
        ElMessage.error('加入采购清单失败，请稍后重试')
      }
    },
    async undoPreference(item: PreferenceUpdateEvent) {
      if (!item.id || item.undone) return
      try {
        await deleteFoodPreference(item.id)
        item.undone = true
        ElMessage.success('已撤销长期偏好')
      } catch {
        ElMessage.error('撤销失败，请稍后重试')
      }
    },
    stopGeneration() {
      this.currentAbort?.abort()
    },
  },
  activated() {
    // 懒人宝典等页面跳转过来时,把待填文本放入输入框(只填充,不自动发送)
    const pendingText = this.agentHandoff.consume()
    if (pendingText) {
      this.input = pendingText
      ElMessage({ message: '已把需求填入输入框,确认后发送即可', type: 'success', grouping: true })
    }
    void this.loadHousehold()
    void this.loadSessions()
    void this.loadChatModels()
    // 刷新后组件重建:从 sessionStorage 恢复当前会话(KeepAlive 内切换页面则 ref 还在,跳过)
    if (!this.sessionId && !this.messages.length) {
      const saved = sessionStorage.getItem(CHAT_SESSION_KEY)
      if (saved) void this.restoreSession(saved)
    }
    this.restoreChatScroll()
  },
  deactivated() {
    return this.rememberChatScroll()
  },
  beforeUnmount() {
    this.rememberChatScroll()
    this.currentAbort?.abort()
    if (this.scrollFrame !== null) cancelAnimationFrame(this.scrollFrame)
  },
  watch: {
    'messages.length'(len) {
      conversationStarted.value = len > 0
    },
  },
})
</script>

<template>
  <section class="workbench">
    <div class="chat-panel">
      <!-- 紧凑会话工具栏:标题只做信息层级，历史管理移入右侧抽屉。 -->
      <div class="session-toolbar">
        <div class="current-session-copy">
          <small>当前对话</small>
          <strong :title="activeSessionTitle">{{ activeSessionTitle }}</strong>
        </div>
        <div class="session-actions">
          <button
            class="history-trigger"
            type="button"
            :disabled="sessionOperationBusy"
            @click="openHistoryDrawer"
          >
            <span>历史对话</span>
            <span v-if="historyCount" class="history-count">{{ historyCount }}</span>
          </button>
          <button
            class="new-session-btn"
            type="button"
            :disabled="sessionOperationBusy"
            @click="startNewSession"
          >
            ＋ 新建对话
          </button>
        </div>
      </div>

      <!-- 消息流:对话即工作台 -->
      <div ref="listEl" class="chat-list" @scroll="onListScroll">
        <div v-if="!messages.length" class="chat-hero">
          <span class="hero-kicker">MEALFLOW AI KITCHEN</span>
          <h2>{{ timeGreeting }}</h2>
          <p>
            把今天想吃什么交给我。人数、预算、冰箱库存和饮食限制，我会一起考虑，给你一套能直接开火的方案。
          </p>
          <div class="hero-chips">
            <el-button size="small" round @click="ask('今晚我们两个人吃，帮我安排一荤一素')">
              <span class="chip-icon">✦</span>两人晚餐 · 一荤一素
            </el-button>
            <el-button size="small" round @click="ask('我不喜欢吃拍黄瓜，帮我换一道素菜')"
              >记住忌口并换一道菜</el-button
            >
            <el-button size="small" round @click="ask('根据冰箱库存，优先用临期食材安排今晚的晚餐')"
              >优先消耗临期食材</el-button
            >
          </div>
        </div>

        <template v-for="bubble in messages" :key="bubble.id">
          <!-- 用户消息:右对齐气泡 -->
          <div v-if="bubble.role === 'user'" class="msg-row user">
            <div class="user-bubble">{{ bubble.content }}</div>
          </div>

          <!-- Agent 消息:思考直播 → 步骤 → 回答 → 方案卡 → 反问 -->
          <div v-if="bubble.modelSwitchLabel" class="model-switch-note" role="status">
            <span class="model-switch-icon" aria-hidden="true">⇄</span>
            <span
              >模型已切换至 <strong>{{ bubble.modelSwitchLabel }}</strong></span
            >
          </div>
          <div v-if="bubble.role === 'assistant'" class="msg-row assistant">
            <div class="assistant-block">
              <div
                v-if="bubble.streaming && !bubble.thinking && !bubble.steps.length"
                class="thinking-line"
              >
                <span class="spinner"></span>
              </div>

              <div v-if="bubble.thinking" class="thinking-box">
                <button
                  class="thinking-toggle"
                  type="button"
                  @click="bubble.thinkingOpen = !bubble.thinkingOpen"
                >
                  <span>{{
                    bubble.streaming && !bubble.thinkingDone ? '✦ 正在思考' : '✦ 思考过程'
                  }}</span>
                  <el-icon class="thinking-chev" :class="{ open: bubble.thinkingOpen }"
                    ><ArrowDown
                  /></el-icon>
                </button>
                <pre v-show="bubble.thinkingOpen" class="thinking-text">{{ bubble.thinking }}</pre>
              </div>

              <!-- 只突出当前真实动作；历史工具信息在完成后按需展开，避免占满对话。 -->
              <div
                v-if="bubble.streaming && activeStep(bubble)"
                class="activity-bar"
                role="status"
                aria-live="polite"
                :class="{ decision: activeStep(bubble)?.step === 'agent_decide' }"
              >
                <span class="activity-orb">✦</span>
                <div class="activity-copy">
                  <strong>{{
                    activeStep(bubble)?.step === 'agent_decide'
                      ? '正在分析需求'
                      : stepLabel(activeStep(bubble)!)
                  }}</strong>
                  <small>{{
                    activeStep(bubble)?.detail ||
                    (activeStep(bubble)?.step === 'agent_decide' ? '判断下一步行动' : '正在处理…')
                  }}</small>
                </div>
                <span class="activity-dots" aria-hidden="true"><i></i><i></i><i></i></span>
              </div>

              <div
                v-if="!bubble.streaming && bubble.toolCalls?.length"
                class="tool-trace-card"
                :class="{ issue: traceHasIssue(bubble) }"
              >
                <button
                  class="trace-summary"
                  type="button"
                  @click="bubble.toolTraceOpen = !bubble.toolTraceOpen"
                >
                  <span :class="traceHasIssue(bubble) ? 'trace-mark issue' : 'trace-mark'">{{
                    traceHasIssue(bubble) ? '!' : '✓'
                  }}</span>
                  <span>{{ traceSummary(bubble) }}</span>
                  <span class="trace-expand">{{ bubble.toolTraceOpen ? '收起' : '查看过程' }}</span>
                </button>
                <ul v-if="bubble.toolTraceOpen">
                  <li
                    v-for="(trace, index) in bubble.toolCalls"
                    :key="`${trace.tool_name}-${index}`"
                  >
                    <span :class="{ failed: trace.status === 'error' }">{{
                      trace.status === 'success' ? '✓' : '!'
                    }}</span>
                    <b>{{ stepLabel({ step: trace.tool_name, state: 'done', detail: '' }) }}</b>
                    <small
                      >{{ trace.output_summary
                      }}<template v-if="trace.duration_ms">
                        · {{ trace.duration_ms }}ms</template
                      ></small
                    >
                  </li>
                </ul>
              </div>

              <div v-if="bubble.generatedRecipes?.length" class="generated-recipes" role="status">
                <span v-for="recipe in bubble.generatedRecipes" :key="recipe.recipe_id">
                  模型生成做法 · {{ recipe.name }}
                </span>
              </div>

              <section v-if="bubble.menuRecommendation" class="menu-recommendation-card">
                <header>
                  <div>
                    <span>本餐建议</span>
                    <strong
                      >{{
                        bubble.menuRecommendation.diners
                          ? `${bubble.menuRecommendation.diners} 人`
                          : ''
                      }}
                      {{
                        bubble.menuRecommendation.meal_structure ||
                        `${bubble.menuRecommendation.ready_count} 道可直接做`
                      }}</strong
                    >
                  </div>
                  <small>约 {{ bubble.menuRecommendation.estimated_parallel_minutes }} 分钟</small>
                </header>
                <div
                  v-for="group in menuSections(bubble.menuRecommendation)"
                  :key="group.label"
                  class="menu-group"
                >
                  <h4>{{ group.label }}</h4>
                  <div class="menu-recommendation-items">
                    <article v-for="item in group.items" :key="item.recipe_id" :class="item.state">
                      <strong>{{ item.name }}</strong>
                      <small v-if="item.source === 'model_generated'" class="generated-source"
                        >模型生成</small
                      >
                      <small v-if="item.target_servings">{{ item.target_servings }} 人份</small>
                      <small v-if="item.state === 'ready'">可直接做</small>
                      <small v-else-if="item.state === 'purchase_optional'"
                        >可选采购：{{ item.missing_main_ingredients.join('、') }}</small
                      >
                      <small v-else>限制：{{ item.restricted_ingredients.join('、') }}</small>
                      <em v-if="ingredientLine(item)">用量：{{ ingredientLine(item) }}</em>
                      <em v-if="item.used_inventory.length"
                        >用库存：{{ item.used_inventory.join('、') }}</em
                      >
                      <em
                        v-for="warning in item.warnings || []"
                        :key="warning"
                        class="menu-warning"
                        >{{ warning }}</em
                      >
                    </article>
                  </div>
                </div>
                <div v-if="stapleOf(bubble.menuRecommendation)" class="menu-group">
                  <h4>主食</h4>
                  <div class="menu-recommendation-items">
                    <article>
                      <strong>{{ stapleOf(bubble.menuRecommendation)?.name }}</strong>
                      <small>{{ stapleOf(bubble.menuRecommendation)?.suggested_quantity }}</small>
                      <small v-if="stapleOf(bubble.menuRecommendation)?.cooked_quantity">{{
                        stapleOf(bubble.menuRecommendation)?.cooked_quantity
                      }}</small>
                      <em v-if="ingredientLine(stapleOf(bubble.menuRecommendation) || {})">{{
                        ingredientLine(stapleOf(bubble.menuRecommendation) || {})
                      }}</em>
                    </article>
                  </div>
                </div>
                <p v-if="bubble.menuRecommendation.portion_note" class="menu-portion-note">
                  {{ bubble.menuRecommendation.portion_note }}
                </p>
              </section>

              <div v-if="bubble.preferenceUpdates?.length" class="preference-updates">
                <div v-for="item in bubble.preferenceUpdates" :key="item.id || item.message">
                  <span>{{ item.undone ? '已撤销长期偏好' : item.message }}</span>
                  <button
                    v-if="item.status === 'saved' && !item.undone"
                    type="button"
                    @click="undoPreference(item)"
                  >
                    撤销
                  </button>
                </div>
              </div>

              <div v-if="bubble.procurementSuggestions?.length" class="procurement-suggestions">
                <strong>缺少的主料（可选）</strong>
                <p>常备辅料已单独标注，不会加入采购清单。</p>
                <div
                  v-for="item in bubble.procurementSuggestions"
                  :key="`${item.source_id}-${item.ingredient}`"
                  class="procurement-suggestion-row"
                >
                  <span
                    >{{ item.ingredient }} <small>{{ item.quantity }}{{ item.unit }}</small></span
                  >
                  <button type="button" :disabled="item.added" @click="addSuggestion(item)">
                    {{ item.added ? '已加入' : '加入采购清单' }}
                  </button>
                </div>
              </div>

              <div
                v-if="bubble.streaming && !bubble.content && !bubble.error && !activeStep(bubble)"
                class="response-placeholder"
              >
                <span class="response-placeholder-dot"></span>
                <span>正在整理建议，回复即将呈现…</span>
              </div>

              <div
                v-if="bubble.content"
                class="assistant-text"
                v-html="renderMarkdown(bubble.content)"
              ></div>
              <p v-if="bubble.stopped" class="stopped-note">⏹ 已停止生成，以上为已输出的内容</p>
              <p v-if="bubble.error" class="error-message">{{ bubble.error }}</p>

              <!-- 食材模糊查询:候选菜列表(仅标注忌口/过敏,不过滤),点击展开做法 -->
              <div v-if="bubble.candidates?.length" class="candidate-card">
                <div class="candidate-card-head">
                  <strong>与「{{ (bubble.candidatesQuery ?? '').slice(0, 30) }}」相关的菜谱</strong>
                  <small>共 {{ bubble.candidates.length }} 道 · 点击查看做法</small>
                </div>
                <div class="candidate-items">
                  <div
                    v-for="item in bubble.candidates"
                    :key="item.recipe_id"
                    class="candidate-row"
                  >
                    <button
                      class="candidate-item"
                      :class="{ expanded: expandedDish === item.name }"
                      @click="openCandidate(item)"
                    >
                      <span class="candidate-line-top">
                        <span class="candidate-name">{{ item.name }}</span>
                        <span class="candidate-tags">
                          <span v-if="item.expiring" class="candidate-tag expiring">临期优先</span>
                          <span
                            v-if="item.conflict_level === 'forbidden'"
                            class="candidate-tag forbidden"
                          >
                            ⚠ {{ item.conflict_summary }}
                          </span>
                          <span
                            v-else-if="item.conflict_level === 'warn'"
                            class="candidate-tag warn"
                          >
                            {{ item.conflict_summary }}
                          </span>
                        </span>
                      </span>
                      <span class="candidate-line-sub">
                        <small class="candidate-ings">{{ item.ingredients_summary }}</small>
                        <small v-if="item.used_fridge?.length" class="candidate-fridge">
                          用冰箱：{{ fridgeUsageText(item) }}
                        </small>
                        <small v-if="item.missing?.length" class="candidate-missing">
                          缺：{{ item.missing.join('、') }}
                        </small>
                      </span>
                    </button>
                    <div
                      v-if="expandedDish === item.name && recipeCache[item.name]"
                      class="recipe-detail"
                    >
                      <div class="recipe-meta">
                        {{ recipeCache[item.name].duration_minutes }} 分钟 ·
                        {{ recipeCache[item.name].kcal_per_serving ?? '—' }} kcal/份 ·
                        {{ recipeCache[item.name].source_name }}
                      </div>
                      <ol>
                        <li
                          v-for="(step, stepIndex) in recipeCache[item.name].steps"
                          :key="stepIndex"
                        >
                          {{ step }}
                        </li>
                      </ol>
                      <table>
                        <thead>
                          <tr>
                            <th>食材</th>
                            <th>用量</th>
                            <th>冰箱</th>
                            <th>参考价</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="row in recipeCache[item.name].ingredients" :key="row.key">
                            <td>{{ row.name }}</td>
                            <td>{{ row.quantity }}{{ row.unit }}</td>
                            <td>{{ row.available ?? 0 }}{{ row.unit }}</td>
                            <td>
                              {{
                                row.reference_price ? `¥${row.reference_price}/${row.unit}` : '—'
                              }}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 本地库未命中:实时搜索到的网络菜谱(下厨房,仅食材清单与步骤梗概) -->
              <div v-if="bubble.webRecipes?.length" class="candidate-card web-recipe-card">
                <div class="candidate-card-head">
                  <strong>实时搜索「{{ bubble.webQuery }}」的网络菜谱</strong>
                  <small>来源：下厨房 · 点击标题查看完整做法</small>
                </div>
                <div class="web-recipe-list">
                  <div v-for="item in bubble.webRecipes" :key="item.url" class="web-recipe-item">
                    <a
                      class="web-recipe-name"
                      :href="item.url"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {{ item.title }}
                      <small v-if="item.rating" class="web-recipe-stats">
                        ★{{ item.rating }} · {{ item.cooked_count }} 人做过
                      </small>
                    </a>
                    <p class="web-recipe-ing">食材：{{ item.ingredients }}</p>
                    <details v-if="item.steps_brief.length" class="web-recipe-steps">
                      <summary>做法梗概（{{ item.steps_brief.length }} 步）</summary>
                      <ol>
                        <li v-for="(step, stepIndex) in item.steps_brief" :key="stepIndex">
                          {{ step }}
                        </li>
                      </ol>
                    </details>
                  </div>
                </div>
                <div class="web-recipe-foot">
                  <a
                    :href="`https://www.xiachufang.com/search/?keyword=${encodeURIComponent(bubble.webQuery ?? '')}`"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    在下厨房查看更多「{{ bubble.webQuery }}」↗
                  </a>
                  <small>网络菜谱为通用做法，未适配忌口与库存，请自行调整。</small>
                </div>
              </div>

              <div v-if="bubble.searchQuery && !bubble.streaming" class="external-recipe">
                <a
                  :href="`https://www.xiachufang.com/search/?keyword=${encodeURIComponent(bubble.searchQuery)}`"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  去下厨房搜索「{{ bubble.searchQuery }}」↗
                </a>
                <small>外部菜谱为通用做法，未适配你的忌口与体质，请自行调整。</small>
              </div>

              <!-- 方案卡:出现在对话流内,决策依据一并融入 -->
              <article v-if="bubble.plan" class="plan-card">
                <div class="plan-head">
                  <el-radio-group v-model="bubble.selectedStrategy" size="small">
                    <el-radio-button
                      v-for="(option, optionIndex) in bubble.plan.options"
                      :key="option.strategy"
                      :value="option.strategy"
                    >
                      {{ optionIndex === 0 ? '★ ' : '' }}{{ option.title }}
                    </el-radio-button>
                  </el-radio-group>
                  <strong class="plan-cost">
                    {{
                      money(
                        bubble.plan.options.find((o) => o.strategy === bubble.selectedStrategy)
                          ?.estimated_cost_cents,
                      )
                    }}
                  </strong>
                </div>

                <template v-for="option in bubble.plan.options" :key="option.strategy">
                  <div v-if="option.strategy === bubble.selectedStrategy" class="plan-body">
                    <p class="strategy-summary">
                      当前：{{ option.title }} · {{ strategySummary(option.strategy) }}
                    </p>
                    <div class="plan-metrics">
                      <span>约 {{ option.estimated_kcal_per_serving ?? '—' }} kcal / 人</span>
                      <span class="divider">·</span>
                      <span>{{ bubble.plan.constraints.expected_diners ?? '—' }} 人</span>
                      <span class="divider">·</span>
                      <span>{{
                        bubble.plan.constraints.budget_limit_cents
                          ? `预算差额 ${money(bubble.plan.constraints.budget_limit_cents - option.estimated_cost_cents)}`
                          : '未设预算'
                      }}</span>
                    </div>

                    <ul class="dish-list">
                      <li v-for="item in option.items" :key="item.name">
                        <button
                          class="dish-name"
                          @click="toggleRecipe(String(item.name), bubble.plan)"
                        >
                          {{ item.name }}
                        </button>
                        <small
                          >{{ item.servings }} 份 ·
                          {{ item.kcal_per_serving ?? '—' }} kcal/份</small
                        >
                        <div
                          v-if="dishWarning(bubble.plan, String(item.name))"
                          class="dish-warning"
                          :class="warningLevel(dishWarning(bubble.plan, String(item.name)))"
                        >
                          {{ dishWarning(bubble.plan, String(item.name)) }}
                        </div>
                        <div
                          v-if="expandedDish === item.name && recipeCache[item.name]"
                          class="recipe-detail"
                        >
                          <div class="recipe-meta">
                            {{ recipeCache[item.name].duration_minutes }} 分钟 ·
                            {{ recipeCache[item.name].source_name }}
                          </div>
                          <ol>
                            <li
                              v-for="(step, stepIndex) in recipeCache[item.name].steps"
                              :key="stepIndex"
                            >
                              {{ step }}
                            </li>
                          </ol>
                          <table>
                            <thead>
                              <tr>
                                <th>食材</th>
                                <th>用量</th>
                                <th>冰箱</th>
                                <th>参考价</th>
                              </tr>
                            </thead>
                            <tbody>
                              <tr v-for="row in recipeCache[item.name].ingredients" :key="row.key">
                                <td>{{ row.name }}</td>
                                <td>{{ row.quantity }}{{ row.unit }}</td>
                                <td>{{ row.available ?? 0 }}{{ row.unit }}</td>
                                <td>
                                  {{
                                    row.reference_price
                                      ? `¥${row.reference_price}/${row.unit}`
                                      : '—'
                                  }}
                                </td>
                              </tr>
                            </tbody>
                          </table>
                        </div>
                      </li>
                    </ul>

                    <el-collapse v-if="option.procurement_list?.length" class="proc-collapse">
                      <el-collapse-item :title="`采购缺口(${option.procurement_list.length} 项)`">
                        <ul class="proc-list">
                          <li v-for="row in option.procurement_list" :key="row.ingredient">
                            {{ row.ingredient }} 缺 {{ row.missing_quantity }}{{ row.unit }}
                          </li>
                        </ul>
                      </el-collapse-item>
                    </el-collapse>
                    <p v-else class="no-proc">冰箱现有食材就够,不用采购 👍</p>

                    <div v-if="option.pantry_notes?.length" class="pantry-notes">
                      <span>⌁</span>
                      <div>
                        <strong>常备料提醒</strong>
                        <p v-for="note in option.pantry_notes" :key="note">{{ note }}</p>
                      </div>
                    </div>

                    <div class="reason-list">
                      <span v-for="reason in option.reasons" :key="reason">{{ reason }}</span>
                    </div>
                  </div>
                </template>

                <div class="plan-foot">
                  <small>价格参考:市区大型超市公开参考价</small>
                  <RouterLink class="procurement-link" to="/admin/procurement"
                    >查看采购清单 ↗</RouterLink
                  >
                  <el-button type="primary" :disabled="sending" @click="confirmPlan(bubble)">
                    确认这套,生成采购清单
                  </el-button>
                </div>
              </article>

              <div v-if="bubble.questions.length && !bubble.streaming" class="chip-row">
                <el-button
                  v-for="question in bubble.questions"
                  :key="question"
                  size="small"
                  round
                  @click="ask(question)"
                >
                  {{ question }}
                </el-button>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 底部输入区:ChatGPT 式 -->
      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          resize="none"
          maxlength="2000"
          placeholder="今天想吃什么，告诉我就好…&#10;例如：明天中午想吃辣子鸡，清淡一些，预算 60 元&#10;我会结合用餐人员、家庭偏好与冰箱食材，为你安排一餐"
          :disabled="sending"
          @keydown.enter.exact.prevent="send()"
        />
        <div class="input-actions">
          <el-popover
            v-model:visible="dinerPickerOpen"
            trigger="click"
            placement="top-start"
            :width="330"
            popper-class="mealflow-diner-popover"
            :disabled="sending"
          >
            <template #reference>
              <button
                class="model-trigger diner-trigger"
                type="button"
                :disabled="sending"
                aria-label="选择本次用餐人员"
                :title="dinerTriggerLabel()"
              >
                <span class="model-spark"
                  ><el-icon><User /></el-icon
                ></span>
                <span>{{ dinerTriggerLabel() }}</span>
                <small v-if="selectedDiners.length" class="diner-count"
                  >{{ selectedDiners.length }} 人</small
                >
                <el-icon class="model-chevron" :class="{ open: dinerPickerOpen }"
                  ><ArrowDown
                /></el-icon>
              </button>
            </template>
            <div class="model-menu-head">
              <strong>这顿饭谁来吃？</strong><span>已选 {{ selectedDiners.length }} 人</span>
            </div>
            <el-checkbox-group v-model="selectedDinerIds" class="diner-options">
              <el-checkbox
                v-for="member in household?.members ?? []"
                :key="member.user_id"
                :value="member.user_id"
                border
                :disabled="sending"
              >
                <span>{{ member.display_name }}</span>
                <small>{{ member.residency === 'resident' ? '常驻' : '访客' }}</small>
              </el-checkbox>
            </el-checkbox-group>
            <p v-if="!household" class="diner-load-hint">家庭档案暂未加载，请重试。</p>
            <el-button v-if="!household" size="small" text @click="loadHousehold"
              >重新加载</el-button
            >
            <p class="diner-note">所选人员与人数将随需求发送；当前规划仍读取全家约束</p>
            <div class="diner-picker-foot">
              <span>已选择 {{ selectedDiners.length }} 人</span>
              <el-button size="small" type="primary" @click="dinerPickerOpen = false"
                >完成选择</el-button
              >
            </div>
          </el-popover>
          <div class="composer-send-actions">
            <el-popover
              v-model:visible="modelMenuOpen"
              trigger="click"
              placement="top-start"
              :width="300"
              popper-class="mealflow-model-popover"
              :disabled="sending"
            >
              <template #reference>
                <button
                  class="model-trigger"
                  type="button"
                  :disabled="sending"
                  aria-label="选择对话模型"
                >
                  <span class="model-spark">{{ '✦' }}</span>
                  <span>{{ selectedModel?.label || '默认模型' }}</span>
                  <el-icon class="model-chevron" :class="{ open: modelMenuOpen }"
                    ><ArrowDown
                  /></el-icon>
                </button>
              </template>
              <div class="model-menu-head">
                <strong>选择模型</strong><span>用于下一条消息</span>
              </div>
              <button
                v-for="model in chatModels"
                :key="model.id"
                class="model-option"
                :class="{ selected: model.id === selectedModelId }"
                type="button"
                :disabled="!model.enabled"
                @click="chooseModel(model)"
              >
                <span class="model-monogram">✦</span>
                <span class="model-option-copy"
                  ><strong
                    >{{ model.label }} <small v-if="model.default">默认</small
                    ><small v-if="model.is_free" class="model-free-badge">免费</small></strong
                  ><span>{{ modelHint(model) }}</span></span
                >
                <span v-if="model.id === selectedModelId" class="model-check">✓</span>
              </button>
              <p v-if="!chatModels.length" class="model-empty">
                模型列表暂不可用 <button type="button" @click="loadChatModels">重试</button>
              </p>
            </el-popover>
            <button
              v-if="!sending"
              class="send-btn"
              type="button"
              :disabled="input.trim().length < 2 || (household !== null && !selectedDiners.length)"
              @click="send()"
            >
              发送 ⏎
            </button>
            <button v-else class="send-btn stop-btn" type="button" @click="stopGeneration">
              ⏹ 停止生成
            </button>
          </div>
        </div>
      </div>
    </div>

    <Teleport to="body">
      <Transition name="history-drawer">
        <div
          v-if="historyDrawerOpen"
          class="history-backdrop"
          @pointerdown.self="historyDrawerOpen = false"
        >
          <aside class="history-drawer-panel" role="dialog" aria-modal="true" aria-label="历史对话">
            <header class="history-drawer-head">
              <div>
                <span class="drawer-kicker">CONVERSATIONS</span>
                <h3>历史对话</h3>
                <p>查找、恢复或整理之前的对话</p>
              </div>
              <button
                class="drawer-close"
                type="button"
                aria-label="关闭历史对话"
                @click="historyDrawerOpen = false"
              >
                <el-icon><Close /></el-icon>
              </button>
            </header>

            <label class="history-search">
              <el-icon><Search /></el-icon>
              <input v-model="historySearch" type="search" placeholder="搜索对话标题" />
            </label>

            <div class="history-drawer-body">
              <div v-if="sessionsLoading" class="history-empty">正在读取历史对话…</div>
              <div v-else-if="!filteredSessions.length" class="history-empty">
                <strong>{{ historySearch ? '没有匹配的对话' : '还没有历史对话' }}</strong>
                <span>{{ historySearch ? '换个关键词再试试' : '完成一次对话后会保存在这里' }}</span>
              </div>
              <section v-for="group in historyGroups" v-else :key="group.key" class="history-group">
                <h4>{{ group.label }}</h4>
                <div
                  v-for="item in group.items"
                  :key="item.session_id"
                  class="history-row"
                  :class="{ active: item.session_id === sessionId }"
                >
                  <button
                    class="history-open"
                    type="button"
                    :disabled="sessionOperationBusy"
                    @click="restoreSession(item.session_id)"
                  >
                    <span class="history-row-title">{{ item.title }}</span>
                    <span class="history-row-meta">
                      <em v-if="item.session_id === sessionId">当前对话</em>
                      <small>{{ relativeTime(item.updated_at) }}</small>
                    </span>
                  </button>
                  <button
                    class="history-delete"
                    type="button"
                    :title="`删除「${item.title}」`"
                    :aria-label="`删除「${item.title}」`"
                    :disabled="sessionOperationBusy"
                    @click.stop="removeSession(item)"
                  >
                    <el-icon v-if="deletingSessionId !== item.session_id"><Delete /></el-icon>
                    <span v-else class="button-spinner"></span>
                  </button>
                </div>
              </section>
            </div>

            <footer class="history-drawer-foot">
              <p>清空历史会保留当前正在查看的对话。</p>
              <button
                class="clear-history-btn"
                type="button"
                :disabled="sessionOperationBusy || historyCount === 0"
                @click="clearHistory"
              >
                <span v-if="clearingHistory" class="button-spinner"></span>
                <el-icon v-else><Delete /></el-icon>
                {{
                  clearingHistory
                    ? '正在清空…'
                    : `清空历史${historyCount ? ` (${historyCount})` : ''}`
                }}
              </button>
            </footer>
          </aside>
        </div>
      </Transition>
    </Teleport>
  </section>
</template>

<style>
/* Element Plus 消息框挂载到 body；层级必须高于历史抽屉(2200)。 */
.history-confirm-overlay.el-overlay {
  z-index: 3100 !important;
}
.history-confirm-box {
  z-index: 3101 !important;
}
</style>

<style scoped>
.workbench {
  height: calc(100vh - 132px);
  display: flex;
  justify-content: center;
}
.chat-panel {
  width: 100%;
  max-width: 820px;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  min-height: 0;
}
/* ===== 会话工具栏 ===== */
.session-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 42px;
  padding: 0 2px 7px;
  border-bottom: 1px solid rgba(211, 220, 215, 0.78);
}
.current-session-copy {
  min-width: 0;
  display: grid;
  gap: 1px;
}
.current-session-copy small {
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.2;
}
.current-session-copy strong {
  max-width: 430px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
  font-size: 13px;
  font-weight: 650;
}
.session-actions {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
}
.history-trigger,
.new-session-btn {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 7px 12px;
  border-radius: 9px;
  font-size: 12.5px;
  font-weight: 650;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background 0.15s ease,
    color 0.15s ease;
}
.history-trigger {
  border: 1px solid transparent;
  background: transparent;
  color: #657872;
}
.history-trigger:hover:not(:disabled) {
  border-color: #d9e5e0;
  background: rgba(255, 255, 255, 0.68);
  color: var(--green);
}
.history-count {
  min-width: 20px;
  height: 20px;
  display: inline-grid;
  place-items: center;
  padding: 0 5px;
  border-radius: 999px;
  background: #e6f1ed;
  color: var(--green);
  font-size: 10.5px;
}
.new-session-btn {
  border: 1px solid var(--green);
  background: var(--green);
  color: #fff;
}
.new-session-btn:hover:not(:disabled) {
  border-color: #17665c;
  background: #17665c;
}
.history-trigger:disabled,
.new-session-btn:disabled {
  opacity: 0.48;
  cursor: not-allowed;
}

/* ===== 覆盖式历史抽屉:不占用工作台正文宽度 ===== */
.history-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2200;
  display: flex;
  justify-content: flex-end;
  background: rgba(20, 34, 30, 0.2);
  backdrop-filter: blur(1.5px);
}
.history-drawer-panel {
  width: min(360px, calc(100vw - 24px));
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 22px 18px 16px;
  background: #fbfaf6;
  border-left: 1px solid #d9e1dc;
  box-shadow: -18px 0 50px rgba(28, 49, 43, 0.13);
}
.history-drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 0 2px 17px;
}
.drawer-kicker {
  color: #2b7b6e;
  font-size: 9.5px;
  font-weight: 750;
  letter-spacing: 0.15em;
}
.history-drawer-head h3 {
  margin: 4px 0 2px;
  color: #173a35;
  font-size: 20px;
  font-weight: 680;
}
.history-drawer-head p {
  margin: 0;
  color: #7d8d88;
  font-size: 12px;
}
.drawer-close {
  width: 32px;
  height: 32px;
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #71817c;
  cursor: pointer;
}
.drawer-close:hover {
  background: #edf2ef;
  color: #164f47;
}
.history-search {
  height: 38px;
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 0 2px 12px;
  padding: 0 11px;
  border: 1px solid #d8e1dc;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.82);
  color: #8b9995;
}
.history-search:focus-within {
  border-color: #75a79d;
  box-shadow: 0 0 0 3px rgba(47, 126, 112, 0.09);
}
.history-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #173a35;
  font: inherit;
  font-size: 13px;
}
.history-search input::placeholder {
  color: #9ca8a4;
}
.history-drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 2px 14px;
  scrollbar-width: thin;
  scrollbar-color: #cbd5d0 transparent;
  overscroll-behavior: contain;
}
.history-empty {
  min-height: 180px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: #93a09c;
  text-align: center;
  font-size: 12px;
}
.history-empty strong {
  color: #5e706a;
  font-size: 13px;
}
.history-group + .history-group {
  margin-top: 15px;
}
.history-group h4 {
  margin: 0 0 5px;
  padding: 0 9px;
  color: #84938e;
  font-size: 10.5px;
  font-weight: 650;
}
.history-row {
  display: flex;
  align-items: center;
  gap: 3px;
  border-radius: 10px;
  transition: background 0.14s ease;
}
.history-row:hover {
  background: #eff4f1;
}
.history-row.active {
  background: #e5f0ec;
}
.history-open {
  min-width: 0;
  flex: 1;
  display: grid;
  gap: 3px;
  padding: 10px 6px 10px 10px;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.history-open:disabled {
  cursor: default;
}
.history-row-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #294841;
  font-size: 13px;
  line-height: 1.35;
}
.history-row.active .history-row-title {
  color: #0f554b;
  font-weight: 650;
}
.history-row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #879690;
}
.history-row-meta em {
  padding: 1px 5px;
  border-radius: 999px;
  background: #d2e7df;
  color: #176256;
  font-size: 9.5px;
  font-style: normal;
}
.history-row-meta small {
  font-size: 10.5px;
}
.history-delete {
  width: 32px;
  height: 32px;
  display: inline-grid;
  place-items: center;
  flex: 0 0 32px;
  margin-right: 5px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #8b9995;
  cursor: pointer;
}
.history-delete:hover:not(:disabled) {
  background: #f8e8e6;
  color: #ae423b;
}
.history-delete:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.history-drawer-foot {
  flex-shrink: 0;
  padding: 13px 2px 0;
  border-top: 1px solid #dfe6e2;
}
.history-drawer-foot p {
  margin: 0 0 9px;
  color: #84928e;
  font-size: 10.5px;
}
.clear-history-btn {
  width: 100%;
  min-height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid #e3c7c3;
  border-radius: 10px;
  background: #fff8f7;
  color: #a53d37;
  font-size: 12.5px;
  font-weight: 650;
  cursor: pointer;
}
.clear-history-btn:hover:not(:disabled) {
  border-color: #ce918b;
  background: #fbecea;
}
.clear-history-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.button-spinner {
  width: 13px;
  height: 13px;
  display: inline-block;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: history-spin 0.75s linear infinite;
}
@keyframes history-spin {
  to {
    transform: rotate(360deg);
  }
}
.history-drawer-enter-active,
.history-drawer-leave-active {
  transition: background 0.2s ease;
}
.history-drawer-enter-active .history-drawer-panel,
.history-drawer-leave-active .history-drawer-panel {
  transition: transform 0.22s ease;
}
.history-drawer-enter-from,
.history-drawer-leave-to {
  background: rgba(20, 34, 30, 0);
}
.history-drawer-enter-from .history-drawer-panel,
.history-drawer-leave-to .history-drawer-panel {
  transform: translateX(100%);
}
.household-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.5rem 0.85rem;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid var(--border);
  border-radius: 14px;
}
.strip-left {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.member-card {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.2rem 0.55rem;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
}
.member-card strong {
  font-size: 13px;
}
.strip-right {
  display: flex;
  gap: 0.4rem;
}
.chat-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
  padding: 0.35rem 12px 0.5rem 0.1rem;
  scrollbar-gutter: stable;
}
.chat-list::-webkit-scrollbar {
  width: 8px;
}
.chat-list::-webkit-scrollbar-track {
  background: transparent;
}
.chat-list::-webkit-scrollbar-thumb {
  background: #d5ddd8;
  border-radius: 8px;
  border: 2px solid transparent;
  background-clip: content-box;
}
.chat-list::-webkit-scrollbar-thumb:hover {
  background-color: #b9c6c0;
}
.chat-hero {
  margin: auto;
  text-align: center;
  max-width: 660px;
  padding: 2.6rem 1rem;
}
.hero-kicker {
  display: inline-block;
  margin-bottom: 12px;
  color: var(--green-2);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.18em;
}
.chat-hero h2 {
  font-family: Georgia, 'Songti SC', serif;
  font-size: 32px;
  margin-bottom: 0.5rem;
  color: var(--ink);
}
.chat-hero p {
  max-width: 560px;
  color: var(--muted);
  margin: 0 auto 1.25rem;
  line-height: 1.8;
}
.hero-chips {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}
.hero-chips .el-button {
  border-color: #d6e4de;
  background: rgba(255, 255, 255, 0.82);
  color: var(--green);
}
.hero-chips .el-button:hover {
  border-color: var(--green-2);
  background: #f3f8f5;
}
.chip-icon {
  margin-right: 4px;
  color: var(--gold);
}
.msg-row {
  display: flex;
}
.model-switch-note {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 2px 0 -4px;
  padding: 0 2px;
  color: #8b9b95;
  font-size: 12px;
  line-height: 1.6;
}
.model-switch-note strong {
  color: #667c72;
  font-weight: 500;
}
.model-switch-icon {
  color: #9baaa3;
  font-size: 14px;
}
.stopped-note {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.msg-row.user {
  justify-content: flex-end;
}
.user-bubble {
  max-width: 78%;
  padding: 0.7rem 1rem;
  border-radius: 16px 16px 4px 16px;
  background: #0f4c45;
  color: #fff;
  line-height: 1.6;
  font-size: 14.5px;
}
.assistant-block {
  width: 100%;
  padding: 0.1rem 0.15rem;
}
.thinking-line {
  margin-bottom: 0.5rem;
  font-size: 12.5px;
  color: #7a8a86;
}
.thinking-line.done {
  color: #9aa8a4;
}
.thinking-box {
  margin-bottom: 0.5rem;
  font-size: 12.5px;
  color: #7a8a86;
}
.thinking-chev {
  margin-left: 4px;
  vertical-align: -2px;
  transition: transform 0.18s ease;
}
.thinking-chev.open {
  transform: rotate(180deg);
}
.thinking-toggle {
  border: 0;
  background: none;
  padding: 0;
  color: inherit;
  font-size: 12.5px;
  cursor: pointer;
  font-weight: 600;
}
.thinking-toggle:hover {
  color: #1f7a6d;
}
.thinking-text {
  white-space: pre-wrap;
  opacity: 0.75;
  max-height: 110px;
  overflow-y: auto;
  border-left: 2px solid #d8e4e0;
  padding-left: 0.55rem;
  margin-top: 0.35rem;
  animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 0.95;
  }
}
.spinner {
  display: inline-block;
  width: 13px;
  height: 13px;
  border: 2px solid #cfdad6;
  border-top-color: #0f4c45;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: 0.65rem;
  vertical-align: -2px;
}
.thinking-live {
  font-weight: 600;
  background: linear-gradient(90deg, #9aa8a4 20%, #1f7a6d 50%, #9aa8a4 80%);
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: shimmer 1.8s linear infinite;
}
@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.activity-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  max-width: 100%;
  margin: 0 0 0.6rem;
  padding: 7px 10px;
  border: 1px solid #e1e8e5;
  border-radius: 8px;
  background: #f7f9f8;
  color: #45655d;
  font-size: 12.5px;
}
.activity-label {
  color: var(--ink);
  font-weight: 650;
  white-space: nowrap;
}
.activity-bar small {
  overflow: hidden;
  color: #81938e;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.activity-bar {
  min-width: 220px;
  padding: 10px 12px;
  gap: 10px;
  border-radius: 12px;
  background: #f6faf8;
  border-color: #e0ece6;
}
.activity-orb {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 9px;
  background: #e6f2ec;
  color: #337c60;
  animation: activity-breathe 1.8s ease-in-out infinite;
}
.activity-copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}
.activity-copy strong {
  color: #395b4e;
  font-size: 12px;
  font-weight: 600;
}
.activity-copy small {
  font-size: 11px;
}
.activity-dots {
  display: flex;
  gap: 3px;
  margin-left: auto;
  padding-left: 12px;
}
.activity-dots i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #7eaa95;
  animation: activity-breathe 1.2s ease-in-out infinite;
}
.activity-dots i:nth-child(2) {
  animation-delay: 0.15s;
}
.activity-dots i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes activity-breathe {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .activity-orb,
  .activity-dots i {
    animation: none;
  }
}
.mini-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 2px solid #cfdad6;
  border-top-color: #1f7a6d;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  margin: 0;
  vertical-align: -1px;
}
.tool-trace-card {
  margin: 0 0 0.7rem;
  border: 1px solid #e1e8e5;
  border-radius: 8px;
  background: #fafcfb;
  color: #56726a;
  font-size: 12px;
  overflow: hidden;
}
.tool-trace-card.issue {
  border-color: #ebd8bd;
  background: #fffdf8;
}
.trace-summary {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 8px;
  padding: 8px 10px;
  border: 0;
  background: transparent;
  color: #58736b;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.trace-summary:hover {
  background: #f3f7f5;
}
.trace-mark {
  display: inline-grid;
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 50%;
  background: #e1f0ea;
  color: #187766;
  font-weight: 750;
}
.trace-mark.issue {
  background: #fdf0df;
  color: #b16a1f;
}
.trace-expand {
  margin-left: auto;
  color: #7a9089;
  white-space: nowrap;
}
.tool-trace-card ul {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 0 10px 10px 34px;
  list-style: none;
}
.tool-trace-card li {
  display: flex;
  gap: 6px;
  align-items: baseline;
}
.tool-trace-card li > span {
  color: #1f7a6d;
  font-weight: 700;
}
.tool-trace-card li > span.failed {
  color: #bd5b4e;
}
.tool-trace-card small {
  color: #7f948e;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.menu-recommendation-card {
  margin: 0 0 0.7rem;
  padding: 12px;
  border: 1px solid #cfe2da;
  border-radius: 10px;
  background: #fbfdfc;
}
.menu-recommendation-card header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.menu-recommendation-card header div {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.menu-recommendation-card header span {
  color: #56726a;
  font-size: 12px;
}
.menu-recommendation-card header strong {
  color: var(--green);
  font-size: 15px;
}
.menu-recommendation-card header small,
.menu-recommendation-card > p {
  color: var(--muted);
  font-size: 12px;
}
.menu-recommendation-card > p {
  margin: 5px 0 9px;
}
.menu-recommendation-items {
  display: grid;
  gap: 6px;
}
.menu-recommendation-items article {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 9px;
  padding: 7px 8px;
  border-radius: 7px;
  background: #f4f8f6;
}
.menu-recommendation-items article.purchase_optional {
  background: #fffaf0;
}
.menu-recommendation-items article.restricted {
  background: #fff4f2;
}
.menu-recommendation-items strong {
  color: var(--ink);
  font-size: 13px;
}
.menu-recommendation-items small {
  color: #69817a;
  font-size: 11.5px;
}
.menu-recommendation-items article.purchase_optional small {
  color: #a16c24;
}
.menu-recommendation-items article.restricted small {
  color: #b4534b;
}
.menu-recommendation-items em {
  grid-column: 1 / -1;
  color: #85958f;
  font-size: 11.5px;
  font-style: normal;
}
.menu-recommendation-items .generated-source {
  color: #0f665b;
  font-weight: 700;
}
.menu-recommendation-items .menu-warning {
  color: #a16c24;
}
.preference-updates {
  margin: 8px 0;
  display: grid;
  gap: 5px;
}
.preference-updates > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 9px;
  border-radius: 8px;
  background: #eef7f3;
  color: #315d54;
  font-size: 12px;
}
.preference-updates button {
  border: 0;
  background: transparent;
  color: #0f665b;
  cursor: pointer;
  font-weight: 700;
}
.generated-recipes {
  margin: 8px 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.generated-recipes span {
  padding: 5px 9px;
  border: 1px solid #bcded5;
  border-radius: 999px;
  background: #eff8f5;
  color: #0f665b;
  font-size: 12px;
  font-weight: 700;
}
.menu-group {
  margin-top: 8px;
}
.menu-group h4 {
  margin: 0 0 4px;
  color: #56726a;
  font-size: 12px;
  font-weight: 600;
}
.menu-relaxation,
.menu-portion-note {
  margin: 4px 0 0;
  color: #85958f;
  font-size: 11.5px;
}
.procurement-suggestions {
  margin: 0 0 0.7rem;
  padding: 10px 12px;
  border: 1px solid #ead9ad;
  border-radius: 10px;
  background: #fffcf4;
  color: #695b3d;
  font-size: 13px;
}
.procurement-suggestions > p {
  margin: 4px 0 8px;
  color: #8a7c5c;
  font-size: 12px;
}
.procurement-suggestion-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 0;
}
.procurement-suggestion-row small {
  color: #8a7c5c;
}
.procurement-suggestion-row button {
  border: 0;
  border-radius: 6px;
  padding: 5px 9px;
  color: #fff;
  background: #155b4e;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.procurement-suggestion-row button:disabled {
  background: #a1b7b0;
  cursor: default;
}
.assistant-text {
  line-height: 1.75;
  font-size: 14.5px;
}
.assistant-text :deep(strong) {
  color: var(--green);
  font-weight: 750;
}
.external-recipe {
  display: grid;
  gap: 4px;
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid #dce9e3;
  border-radius: 10px;
  background: #f7fbf8;
}
.external-recipe a {
  color: var(--green);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}
.external-recipe a:hover {
  text-decoration: underline;
}
.external-recipe small {
  color: var(--muted);
  font-size: 11px;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
  margin-top: 0.6rem;
}
.plan-card {
  margin-top: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 0.9rem 1rem;
  background: linear-gradient(145deg, #fff 0%, #fbfdfb 100%);
  box-shadow: 0 8px 24px rgba(33, 60, 54, 0.05);
}
.response-placeholder {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  margin: 0.15rem 0 0.75rem;
  padding: 9px 13px;
  border: 1px solid #dce9e3;
  border-radius: 11px;
  background: linear-gradient(90deg, #f6faf8, #fff);
  color: #6f817b;
  font-size: 12.5px;
  animation: response-in 0.24s ease-out;
}
.response-placeholder-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green-2);
  box-shadow: 0 0 0 5px rgba(23, 114, 103, 0.1);
  animation: response-pulse 1.25s ease-in-out infinite;
}
@keyframes response-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
@keyframes response-pulse {
  0%,
  100% {
    opacity: 0.42;
  }
  50% {
    opacity: 1;
  }
}
.plan-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}
.plan-cost {
  font-size: 19px;
  font-family: Georgia, serif;
}
.plan-body {
  border-top: 1px dashed var(--border);
  padding-top: 0.6rem;
}
.strategy-summary {
  margin: 0 0 0.55rem;
  padding: 6px 9px;
  border-radius: 8px;
  background: #f3f8f5;
  color: var(--green);
  font-size: 12px;
}
.plan-metrics {
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 0.55rem;
}
.plan-metrics .divider {
  margin: 0 0.4rem;
}
.dish-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.dish-name {
  border: 0;
  background: none;
  padding: 0;
  font-weight: 650;
  color: var(--ink);
  cursor: pointer;
  text-decoration: underline dotted;
  font-size: 14.5px;
}
.dish-list small {
  color: var(--muted);
  margin-left: 0.4rem;
}
.dish-warning {
  font-size: 12px;
  padding: 4px 9px;
  border-radius: 8px;
  margin-top: 0.3rem;
}
.dish-warning.warn {
  background: #fdf3e0;
  color: #8a5a12;
}
.dish-warning.forbidden {
  background: #fdeceb;
  color: #a43c36;
}
.recipe-detail {
  margin-top: 0.45rem;
  padding: 0.6rem 0.7rem;
  background: #f6faf8;
  border-radius: 10px;
  font-size: 13px;
}
.recipe-meta {
  color: var(--muted);
  margin-bottom: 0.3rem;
}
.recipe-detail ol {
  margin: 0 0 0.4rem 1.1rem;
}
.recipe-detail table {
  width: 100%;
  font-size: 12px;
}
.proc-collapse {
  margin: 0.55rem 0 0;
}
.proc-collapse :deep(.el-collapse-item__header) {
  font-weight: 600;
  color: #0f4c45;
}
.proc-list {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 13px;
  color: var(--ink);
}
.no-proc {
  font-size: 13px;
  color: var(--muted);
  margin-top: 0.5rem;
}
.pantry-notes {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  margin-top: 0.6rem;
  padding: 8px 10px;
  border: 1px solid #e8dfc7;
  border-radius: 9px;
  background: #fffaf0;
  color: #866b31;
  font-size: 12px;
}
.pantry-notes > span {
  color: #c58d2d;
  font-size: 17px;
  line-height: 1;
}
.pantry-notes strong {
  display: block;
  margin-bottom: 2px;
}
.pantry-notes p {
  margin: 0;
  line-height: 1.55;
}
.reason-list {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 12px;
  color: var(--muted);
  margin-top: 0.55rem;
}
.plan-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.75rem;
  padding-top: 0.6rem;
  border-top: 1px dashed var(--border);
}
.plan-foot small {
  color: #9aa8a4;
}
/* ===== 食材模糊查询:候选菜列表卡(与方案卡同视觉语言,仅标注不过滤) ===== */
.candidate-card {
  margin-top: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: linear-gradient(145deg, #fff, #fbfdfb);
  box-shadow: 0 8px 24px rgba(33, 60, 54, 0.05);
  overflow: hidden;
}
/* 实时搜索的网络菜谱卡(下厨房):样式基调与候选卡一致 */
.web-recipe-list {
  display: grid;
}
.web-recipe-item {
  padding: 0.65rem 0.95rem;
  display: grid;
  gap: 5px;
}
.web-recipe-item + .web-recipe-item {
  border-top: 1px solid #f0f4f1;
}
.web-recipe-name {
  color: #21403a;
  font-size: 13.5px;
  font-weight: 700;
  text-decoration: none;
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.web-recipe-name:hover {
  color: var(--green);
  text-decoration: underline;
}
.web-recipe-stats {
  color: #a2701d;
  font-size: 11px;
  font-weight: 600;
}
.web-recipe-ing {
  margin: 0;
  font-size: 12.5px;
  color: #3d524c;
}
.web-recipe-steps {
  font-size: 12.5px;
  color: #3d524c;
}
.web-recipe-steps summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 12px;
}
.web-recipe-steps ol {
  margin: 6px 0 0;
  padding-left: 22px;
  display: grid;
  gap: 4px;
}
.web-recipe-foot {
  display: grid;
  gap: 3px;
  padding: 0.6rem 0.95rem;
  border-top: 1px solid #eef4f0;
  background: #f7fbf8;
}
.web-recipe-foot a {
  color: var(--green);
  font-size: 12.5px;
  font-weight: 700;
  text-decoration: none;
}
.web-recipe-foot a:hover {
  text-decoration: underline;
}
.web-recipe-foot small {
  color: var(--muted);
  font-size: 11px;
}
.candidate-card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.6rem 0.95rem 0.5rem;
  border-bottom: 1px solid #eef4f0;
}
.candidate-card-head strong {
  font-size: 13.5px;
  color: var(--green);
}
.candidate-card-head small {
  color: var(--muted);
  font-size: 11.5px;
}
.candidate-items {
  display: grid;
}
.candidate-row + .candidate-row {
  border-top: 1px solid #f0f4f1;
}
.candidate-item {
  width: 100%;
  display: block;
  padding: 0.55rem 0.95rem;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}
.candidate-item:hover {
  background: #f4faf7;
}
.candidate-item.expanded {
  background: #eef7f2;
}
.candidate-line-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.55rem;
}
.candidate-name {
  flex-shrink: 0;
  font-weight: 650;
  font-size: 14px;
  color: var(--ink);
  white-space: nowrap;
}
.candidate-item:hover .candidate-name {
  color: var(--green-2);
}
.candidate-line-sub {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem 0.7rem;
  margin-top: 3px;
}
.candidate-ings {
  color: var(--muted);
  font-size: 12px;
}
.candidate-fridge {
  color: #0f6b4f;
  font-size: 12px;
}
.candidate-missing {
  color: #a15b21;
  font-size: 12px;
}
.candidate-tags {
  display: flex;
  gap: 5px;
  flex-shrink: 0;
}
.candidate-tag {
  padding: 2.5px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.candidate-tag.forbidden {
  background: #fdeceb;
  color: #a43c36;
}
.candidate-tag.warn {
  background: #fdf3e0;
  color: #8a5a12;
}
.candidate-tag.expiring {
  background: #e7f2ee;
  color: #0f6b4f;
}
.candidate-row .recipe-detail {
  margin: 0 0.95rem 0.6rem;
}
.chat-input {
  flex-shrink: 0;
  border: 1px solid var(--border);
  border-radius: 16px;
  background: #fff;
  padding: 0.6rem 0.7rem 0.5rem;
  box-shadow: 0 6px 24px rgba(33, 60, 54, 0.07);
}
.model-trigger {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-width: 166px;
  height: 34px;
  padding: 0 11px 0 8px;
  border: 1px solid #dbe8e2;
  border-radius: 10px;
  background: linear-gradient(180deg, #ffffff, #f7fbf9);
  color: #24483e;
  font-size: 12px;
  font-weight: 650;
  box-shadow: 0 2px 8px rgba(38, 83, 70, 0.05);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}
.model-trigger:hover:not(:disabled) {
  border-color: #9bc8b5;
  box-shadow: 0 5px 14px rgba(38, 83, 70, 0.11);
  transform: translateY(-1px);
}
.model-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.62;
}
.model-spark {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 7px;
  background: #e1f1e9;
  color: #0f7555;
  font-size: 13px;
}
.model-chevron {
  margin-left: auto;
  color: #78948a;
  transition: transform 0.18s ease;
}
.model-chevron.open {
  transform: rotate(180deg);
}
.model-menu-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: -2px 0 8px;
}
.model-menu-head strong {
  color: #24483e;
  font-size: 13px;
}
.model-menu-head span {
  color: #91a59e;
  font-size: 11px;
}
.model-option {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 9px;
  padding: 9px 8px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #294c42;
  text-align: left;
  transition: background 0.15s ease;
}
.model-option:hover:not(:disabled),
.model-option.selected {
  background: #eef8f3;
}
.model-option:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
.model-monogram {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border-radius: 9px;
  background: #e8f3ee;
  color: #147354;
  font-size: 12px;
  font-weight: 800;
}
.model-option-copy {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 2px;
}
.model-option-copy strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.model-option-copy strong small {
  margin-left: 4px;
  color: #19815d;
  font-size: 10px;
  font-weight: 700;
}
.model-option-copy strong .model-free-badge {
  color: #197a55;
  background: #e5f6ec;
  border-radius: 5px;
  padding: 2px 5px;
}
.model-option-copy span {
  overflow: hidden;
  color: #8a9e97;
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-check {
  color: #13815c;
  font-size: 15px;
  font-weight: 800;
}
.model-empty {
  margin: 6px 2px 0;
  color: #8a9e97;
  font-size: 11px;
}
.model-empty button {
  padding: 0;
  border: 0;
  background: transparent;
  color: #13815c;
  cursor: pointer;
}
:global(.mealflow-model-popover.el-popper) {
  padding: 12px !important;
  border: 1px solid #dce9e3;
  border-radius: 14px;
  box-shadow: 0 16px 40px rgba(31, 73, 61, 0.16);
}
:global(.mealflow-model-popover .model-menu-head) {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: -2px 0 8px;
}
:global(.mealflow-model-popover .model-menu-head strong) {
  color: #24483e;
  font-size: 13px;
}
:global(.mealflow-model-popover .model-menu-head span) {
  color: #91a59e;
  font-size: 11px;
}
:global(.mealflow-model-popover .model-option) {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 9px;
  padding: 9px 8px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: #294c42;
  text-align: left;
}
:global(.mealflow-model-popover .model-option:hover:not(:disabled)),
:global(.mealflow-model-popover .model-option.selected) {
  background: #eef8f3;
}
:global(.mealflow-model-popover .model-option-copy) {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 2px;
}
:global(.mealflow-model-popover .model-option-copy strong) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
:global(.mealflow-model-popover .model-option-copy span) {
  overflow: hidden;
  color: #8a9e97;
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
:global(.mealflow-model-popover .model-monogram) {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border-radius: 9px;
  background: #e8f3ee;
  color: #147354;
  font-size: 12px;
  font-weight: 800;
}
.diner-trigger {
  min-width: 150px;
}
:global(.mealflow-diner-popover.el-popper) {
  padding: 12px !important;
  border: 1px solid #dce9e3;
  border-radius: 14px;
  box-shadow: 0 16px 40px rgba(31, 73, 61, 0.16);
}
:global(.mealflow-diner-popover .model-menu-head) {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: -2px 0 8px;
}
:global(.mealflow-diner-popover .model-menu-head strong) {
  color: #24483e;
  font-size: 13px;
}
:global(.mealflow-diner-popover .model-menu-head span) {
  color: #91a59e;
  font-size: 11px;
}
:global(.mealflow-diner-popover .diner-options) {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
}
:global(.mealflow-diner-popover .diner-options .el-checkbox) {
  width: 100%;
  margin: 0;
  padding: 8px 10px;
  border-radius: 9px;
}
:global(.mealflow-diner-popover .diner-options .el-checkbox__label) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  gap: 8px;
  font-size: 12px;
}
:global(.mealflow-diner-popover .diner-options .el-checkbox__label small) {
  color: var(--muted);
  font-size: 10px;
}
:global(.mealflow-diner-popover .diner-load-hint) {
  font-size: 12px;
  color: var(--muted);
}
:global(.mealflow-diner-popover .diner-note) {
  margin: 8px 2px 0;
  color: #8a9e97;
  font-size: 11px;
}
:global(.mealflow-diner-popover .diner-picker-foot) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
  color: var(--muted);
  font-size: 11px;
}
.procurement-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--green);
  text-decoration: none;
  white-space: nowrap;
}
.procurement-link:hover {
  text-decoration: underline;
}
@media (prefers-reduced-motion: reduce) {
  .response-placeholder,
  .response-placeholder-dot,
  .thinking-live,
  .button-spinner {
    animation: none;
  }
  .history-drawer-enter-active,
  .history-drawer-leave-active,
  .history-drawer-enter-active .history-drawer-panel,
  .history-drawer-leave-active .history-drawer-panel {
    transition: none;
  }
}
.chat-input :deep(.el-textarea__inner) {
  font-size: 14.5px;
  line-height: 1.6;
  border-radius: 10px;
  box-shadow: none;
}
.input-actions {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 12px;
  margin-top: 0.4rem;
}
.composer-send-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  flex-shrink: 0;
}
.composer-send-actions .send-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  cursor: pointer;
  font-family: inherit;
  min-width: 82px;
  height: 34px;
  padding: 0 14px;
  border: 1px solid #176f52;
  border-radius: 10px;
  background: #176f52;
  box-shadow: 0 5px 14px rgba(23, 111, 82, 0.18);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.01em;
  transition:
    background 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}
.composer-send-actions .send-btn:hover:not(:disabled) {
  border-color: #0f6046;
  background: #0f6046;
  box-shadow: 0 8px 18px rgba(23, 111, 82, 0.24);
  transform: translateY(-1px);
}
.composer-send-actions .send-btn:active:not(:disabled) {
  box-shadow: 0 3px 8px rgba(23, 111, 82, 0.16);
  transform: translateY(0);
}
.composer-send-actions .send-btn:disabled {
  cursor: not-allowed;
  border-color: #c5d8cf;
  background: #c5d8cf;
  box-shadow: none;
}
.composer-send-actions .send-btn.stop-btn {
  border-color: #78988b;
  background: #f5f9f7;
  color: #4c6d60;
  box-shadow: none;
}
.composer-send-actions .send-btn.stop-btn:hover:not(:disabled) {
  border-color: #a5bdb3;
  background: #edf5f1;
  box-shadow: 0 5px 14px rgba(52, 91, 76, 0.1);
}
.composer-send-actions .send-btn:focus-visible {
  outline: 2px solid #7eb69f;
  outline-offset: 3px;
}
.diner-count {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 500;
  color: #78948a;
}
.diner-trigger {
  max-width: min(420px, 46vw);
}
.diner-trigger > span:nth-child(2) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.error-message {
  margin-top: 0.4rem;
}
@media (max-width: 640px) {
  .input-actions {
    align-items: stretch;
    flex-wrap: wrap;
  }
  .composer-send-actions {
    max-width: 100%;
  }
  .composer-send-actions .model-trigger {
    min-width: 0;
  }
  .diner-trigger {
    min-width: 0;
    max-width: 100%;
  }
  .session-toolbar {
    align-items: flex-start;
    gap: 10px;
  }
  .current-session-copy strong {
    max-width: 42vw;
  }
  .history-trigger,
  .new-session-btn {
    padding: 7px 9px;
  }
  .history-drawer-panel {
    width: min(100vw, 430px);
    padding: 18px 14px 14px;
    border-left: 0;
  }
}
</style>
