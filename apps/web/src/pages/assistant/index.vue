<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import ChatContent from '../../components/ChatContent.vue'
import { session } from '../../shared/session'
import { streamRun, type AgentEvent } from '../../shared/stream'
import { request } from '../../shared/transport'
import { takeAssistantPrompt } from '../../shared/assistantPrompt'
import {
  formatOfflineRecipe, formatOfflineSkill, parseOfflineBundle,
  searchOffline, type OfflineBundle,
} from '../../shared/offlineSearch'

interface Action { id: string; type: string; payload: Record<string, unknown>; payload_hash: string; version: number }
interface Evidence { id: string; kind: string; title?: string; source_url?: string; source_name?: string; notes?: string; content?: { name?: string; title?: string; source_name?: string; steps?: string[]; markdown?: string; snippet?: string; conversion_factor?: string; unit?: string; severity?: string; applies_to?: string[] } }
interface AssistantOption { key: string; label: string; example: string; recipe_id?: string; reason: string; missing?: string[]; prompt: string }
interface Result { text?: string; pending_action?: Action; action_result?: { status: string }; warnings?: string[]; status?: string; citations?: string[]; evidence?: Evidence[]; thinking_steps?: string[]; options?: AssistantOption[] }
interface Run { run_id: string; text: string; status: string; result: Result; error_code?: string; steps: string[]; expanded: boolean; visibleText: string; typing: boolean; typingTimer: number | null; error: string; deciding: boolean; requestKey: string }
interface QuickResult { template: string; text: string; data_version?: string; check?: { line: string } }
interface Chat { id: string; title: string; updated_at: string; status: string }
interface Detail { session_id: string; has_more: boolean; runs: Run[] }
const active = (status: string) => ['queued', 'running', 'cancel_requested'].includes(status)
const toolNames: Record<string, string> = { get_inventory_summary: '查询冰箱库存', get_member_constraints: '读取家庭限制', recommend_menu: '整理菜单', prepare_procurement_action: '整理采购建议', get_preference_summary: '查询家庭偏好', prepare_preference_action: '整理偏好建议', search_recipes: '搜索菜谱', search_cooking_skills: '查询烹饪技能', find_ingredient_substitute: '查询食材替换', get_flavor_template: '查询风味模板', check_food_risk: '查询禁忌风险', get_recipe_detail: '读取菜谱', check_recipe_availability: '核对食材' }

export default defineComponent({
  name: 'AssistantPage',
  components: { AppHeader, ChatContent },
  data() {
    return {
      session, input: '', sessionId: '', runs: [] as Run[], chats: [] as Chat[],
      historyOpen: false, historyLoading: false, historyMore: false, hasMore: false,
      loading: false, sending: false, error: '', scrollTarget: '', followBottom: true, conversationRefreshing: false,
      offline: false, quickLoading: false, quickResult: null as QuickResult | null, quickError: '',
      quickGeneration: 0,
      cursor: 0, reconnects: 0, generation: 0,
      conversationHeight: 0, composerFocused: false,
      stopStream: null as null | (() => void),
      suggestions: ['冰箱里还有鸡蛋吗？', '把牛奶加入采购清单', '今晚吃什么？'],
    }
  },
  computed: {
    busy(): boolean { return this.sending || this.runs.some((run) => active(run.status)) },
    storageKey(): string { return `mealflow.chat.${session.userId}.${session.householdId}` },
    historyBase(): string { return `/households/${session.householdId}/agent/sessions` },
  },
  watch: {
    'session.householdId'() { this.restore() },
    'session.userId'() { this.restore() },
    'session.restored'() { this.restore() },
  },
  onShow() {
    if (!this.sessionId && !this.loading) this.restore()
    const prompt = takeAssistantPrompt()
    if (prompt) {
      this.input = prompt
      void this.$nextTick(() => { this.composerFocused = true })
    }
    void this.$nextTick(() => {
      uni.createSelectorQuery().in(this).select('.conversation').boundingClientRect((rect) => {
        this.conversationHeight = (rect as { height: number }).height
      }).exec()
    })
  },
  onUnload() { this.generation++; this.stopStream?.(); this.runs.forEach((run) => this.stopTyping(run)) },
  methods: {
    refreshConversation() {
      if (!this.sessionId || this.busy) { this.conversationRefreshing = false; return }
      this.conversationRefreshing = true
      const id = this.sessionId
      return request<Detail>(`${this.historyBase}/${id}`).then((data) => {
        if (id !== this.sessionId) return
        // A manual refresh replaces visible history only after the request succeeds.
        this.runs = data.runs.map(this.decorate)
        this.hasMore = data.has_more
        this.error = ''
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.conversationRefreshing = false })
    },
    activeStatus(status: string): boolean { return active(status) },
    label(status: string): string {
      return ({ queued: '等待开始', running: '正在回答', cancel_requested: '正在停止', succeeded: '已完成', failed: '未完成', cancelled: '已停止', waiting_input: '等待补充信息', waiting_confirmation: '等待确认' } as Record<string, string>)[status] || status
    },
    statusLabel(run: Run): string { return run.typing ? '正在回答' : this.label(run.status) },
    reset() {
      this.generation++
      this.stopStream?.()
      this.stopStream = null
      this.runs.forEach((run) => this.stopTyping(run))
      this.sessionId = ''
      this.runs = []
      this.cursor = 0
      this.reconnects = 0
      this.error = ''
      this.sending = false
      this.loading = false
      this.hasMore = false
      this.followBottom = true
    },
    restore() {
      this.reset()
      this.chats = []
      this.historyOpen = false
      if (!session.restored || !session.userId || !session.householdId) return
      const saved = uni.getStorageSync(this.storageKey) as string
      if (saved) void this.openChat(saved)
    },
    newChat() {
      // The worker continues in the background; history can reopen its running task.
      this.reset()
      uni.removeStorageSync(this.storageKey)
      this.historyOpen = false
    },
    scrollBottom(force = false) {
      if (!force && !this.followBottom) return
      this.scrollTarget = ''
      void this.$nextTick(() => { this.scrollTarget = 'conversation-end' })
    },
    onScroll(event: { detail: { scrollHeight: number; scrollTop: number } }) {
      this.followBottom = event.detail.scrollHeight - event.detail.scrollTop < this.conversationHeight + 120
    },
    decorate(run: Run): Run {
      return {
        ...run,
        steps: run.result?.thinking_steps || [],
        expanded: false,
        visibleText: this.cleanText(run.result?.text || ''),
        typing: false,
        typingTimer: null,
        error: '',
        deciding: false,
        requestKey: '',
      }
    },
    openChat(id: string) {
      this.reset()
      this.loading = true
      this.historyOpen = false
      const epoch = this.generation
      return request<Detail>(`${this.historyBase}/${id}`).then((data) => {
        if (epoch !== this.generation) return
        this.sessionId = data.session_id
        this.runs = data.runs.map(this.decorate)
        this.hasMore = data.has_more
        uni.setStorageSync(this.storageKey, this.sessionId)
        this.scrollBottom(true)
        const running = this.runs.find((run) => active(run.status))
        if (running) this.consume(running)
      }, (error: Error) => {
        if (epoch !== this.generation) return
        this.error = error.message
      }).finally(() => { if (epoch === this.generation) this.loading = false })
    },
    loadOlder() {
      if (this.loading || !this.runs.length) return
      this.loading = true
      const epoch = this.generation
      const anchor = this.runs[0].run_id
      return request<Detail>(`${this.historyBase}/${this.sessionId}?before=${anchor}`).then((data) => {
        if (epoch !== this.generation) return
        this.runs = [...data.runs.map(this.decorate), ...this.runs]
        this.hasMore = data.has_more
        void this.$nextTick(() => { this.scrollTarget = `message-${anchor}` })
      }, (error: Error) => { if (epoch === this.generation) this.error = error.message })
        .finally(() => { if (epoch === this.generation) this.loading = false })
    },
    showHistory() { this.historyOpen = true; void this.loadHistory(false) },
    loadHistory(more: boolean) {
      if (this.historyLoading) return
      this.historyLoading = true
      const epoch = this.generation
      return request<Chat[]>(`${this.historyBase}?offset=${more ? this.chats.length : 0}`).then((rows) => {
        if (epoch !== this.generation) return
        this.chats = more ? [...this.chats, ...rows] : rows
        this.historyMore = rows.length === 30
      }, (error: Error) => { if (epoch === this.generation) this.error = error.message })
        .finally(() => { this.historyLoading = false })
    },
    deleteChat(chat: Chat) {
      uni.showModal({ title: '删除对话', content: '此对话将从历史列表移除，已确认的采购等业务记录会保留。', success: (choice) => {
        if (!choice.confirm) return
        void request(`${this.historyBase}/${chat.id}`, undefined, true, {}, 'DELETE').then(() => {
          if (this.sessionId === chat.id) this.newChat()
          this.chats = this.chats.filter((item) => item.id !== chat.id)
        }, (error: Error) => { this.error = error.message })
      } })
    },
    chooseOption(option: AssistantOption) {
      if (this.busy || this.loading || !option.prompt) return
      this.input = option.prompt
      this.send()
    },
    send() {
      const text = this.input.trim()
      if (!text || this.busy || this.loading || !session.householdId) return
      this.runs.forEach((item) => { if (item.typing) this.finishTyping(item) })
      const run: Run = {
        run_id: '', text, status: 'queued', result: {}, steps: [], expanded: true,
        visibleText: '', typing: false, typingTimer: null, error: '', deciding: false,
        requestKey: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      }
      this.runs.push(run)
      this.input = ''
      this.followBottom = true
      // Use Vue's reactive row so asynchronous events update the rendered message.
      void this.submit(this.runs[this.runs.length - 1])
    },
    submit(run: Run) {
      this.sending = true
      this.stopTyping(run)
      run.error = ''
      run.status = 'queued'
      if (!run.run_id) {
        run.visibleText = ''
        run.steps = []
      }
      const epoch = this.generation
      this.scrollBottom(true)
      return request<{ run_id: string; session_id: string }>('/agent/turn-runs', {
        household_id: session.householdId, session_id: this.sessionId || null, text: run.text,
      }, true, { 'Idempotency-Key': run.requestKey }).then((data) => {
        if (epoch !== this.generation) return
        run.run_id = data.run_id
        this.sessionId = data.session_id
        uni.setStorageSync(this.storageKey, this.sessionId)
        this.cursor = 0
        this.reconnects = 0
        this.consume(run)
      }, (error: Error) => {
        if (epoch !== this.generation) return
        run.status = 'failed'
        run.error = error.message
      }).finally(() => { if (epoch === this.generation) this.sending = false })
    },
    consume(run: Run) {
      const previous = this.stopStream
      this.stopStream = null
      previous?.()
      const epoch = this.generation
      this.stopStream = streamRun(run.run_id, this.cursor, (event: AgentEvent) => {
        if (epoch !== this.generation) return
        this.cursor = event.seq
        if (event.type === 'status') run.status = String(event.payload.status)
        if (event.type === 'tool_started' || event.type === 'tool_finished') {
          const name = String(event.payload.name)
          run.steps.push(`${toolNames[name] || '处理家庭记录'}${event.type === 'tool_finished' ? ' · 完成' : '…'}`)
          run.expanded = true
        }
        if (event.type === 'thinking' && Array.isArray(event.payload.steps)) {
          const steps = event.payload.steps.map(String)
          run.steps = [...run.steps, ...steps.filter((step) => !run.steps.includes(step))]
          run.expanded = true
        }
        if (typeof event.payload.delta === 'string') {
          run.result.text = (run.result.text || '') + event.payload.delta
          if (!run.typing) run.visibleText = this.cleanText(run.result.text)
        }
        if (event.type === 'action_required') run.result.pending_action = event.payload as unknown as Action
        if (['completed', 'failed', 'cancelled'].includes(event.type)) {
          run.result = event.payload as Result
          run.status = String(event.payload.status || event.type)
          if (run.status === 'failed') run.error = run.result.text || '本次任务未完成，请检查状态或重试。'
          this.present(run)
        }
        this.scrollBottom()
      }, () => {
        if (epoch !== this.generation || !active(run.status)) return
        this.reconnects++
        if (this.reconnects > 3) { run.error = '连接中断，请检查任务状态。'; return }
        void this.checkRun(run)
      })
    },
    checkRun(run: Run) {
      const epoch = this.generation
      run.error = ''
      return request<{ status: string; result: Result }>(`/agent/turn-runs/${run.run_id}`).then((data) => {
        if (epoch !== this.generation) return
        run.status = data.status
        run.result = data.result
        if (active(run.status)) this.consume(run)
        else this.present(run)
        this.scrollBottom()
      }, (error: Error) => { if (epoch === this.generation) run.error = error.message })
    },
    retry(run: Run) {
      if (this.busy) return
      if (!run.run_id) { void this.submit(run); return }
      this.input = run.text
      this.send()
    },
    cancel() {
      const run = this.runs.find((item) => active(item.status) && item.run_id)
      if (!run) return
      return request(`/agent/turn-runs/${run.run_id}/cancel`, {}).then(() => {
        run.status = 'cancel_requested'
      }, (error: Error) => { run.error = error.message })
    },
    decide(run: Run, accept: boolean) {
      const action = run.result.pending_action
      if (!action || run.deciding) return
      run.deciding = true
      const epoch = this.generation
      const decision = accept ? 'confirm' : 'reject'
      return request(`/actions/${action.id}/${decision}`, {
        expected_version: action.version, payload_hash: action.payload_hash,
      }, true, { 'Idempotency-Key': `${action.id}-${action.version}-${decision}` }).then(() => {
        if (epoch === this.generation) return this.checkRun(run)
      }, (error: Error) => { if (epoch === this.generation) run.error = error.message })
        .finally(() => { run.deciding = false })
    },
    copy(text: string) { uni.setClipboardData({ data: text }) },
    cleanText(value: string): string {
      return value
        .replace(/[ \t]*\[((?:recipe|lot|knowledge|web|substitution|flavor|risk|log):[^\]]+)\][ \t]*/g, ' ')
        .replace(/^[ \t]*(?:来源|参考来源|出处)[ \t]*[：:][ \t]*(?:\r?\n|$)/gm, '')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/[ \t]{2,}/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim()
    },
    displayText(run: Run): string {
      return this.cleanText(run.result.text || '') || run.visibleText
    },
    stopTyping(run: Run) {
      if (run.typingTimer !== null) {
        clearTimeout(run.typingTimer)
        clearInterval(run.typingTimer)
        run.typingTimer = null
      }
      run.typing = false
    },
    finishTyping(run: Run) {
      this.stopTyping(run)
      run.visibleText = this.cleanText(run.result.text || '')
      this.scrollBottom()
    },
    present(run: Run) {
      const target = this.cleanText(run.result.text || '')
      const newSteps = (run.result.thinking_steps || []).filter((step) => !run.steps.includes(step))
      const hadSteps = run.steps.length > 0
      if (!hadSteps && newSteps.length) {
        run.steps = newSteps
        run.expanded = true
      }
      if (run.status === 'failed' || run.status === 'cancelled') {
        this.stopTyping(run)
        run.visibleText = target
        return
      }
      if (!target) {
        this.stopTyping(run)
        run.visibleText = ''
        return
      }
      if (run.typing) return
      if (run.visibleText === target) {
        this.stopTyping(run)
        return
      }
      if (!target.startsWith(run.visibleText)) run.visibleText = ''
      this.revealText(run, target, !hadSteps && newSteps.length ? 450 : 0)
    },
    revealText(run: Run, target: string, delay: number) {
      this.stopTyping(run)
      const epoch = this.generation
      run.typing = true
      const start = () => {
        if (epoch !== this.generation) { this.stopTyping(run); return }
        run.typingTimer = setInterval(() => {
          if (epoch !== this.generation) { this.stopTyping(run); return }
          if (run.visibleText.length >= target.length) { this.stopTyping(run); return }
          // One visible character per tick keeps the reply readable and stream-like.
          run.visibleText = target.slice(0, run.visibleText.length + 1)
          if (run.visibleText.length % 16 === 0) this.scrollBottom()
          if (run.visibleText.length >= target.length) {
            this.stopTyping(run)
            this.scrollBottom()
          }
        }, 16) as unknown as number
      }
      if (delay > 0) run.typingTimer = setTimeout(start, delay) as unknown as number
      else start()
    },
    toggleOffline(event: Event) {
      this.offline = (event as CustomEvent<{ value: boolean }>).detail.value
      if (this.offline && !this.loadOfflineBundle()) {
        uni.showToast({ title: '尚未保存离线包', icon: 'none' })
      }
    },
    offlineStorageKey(): string { return `mealflow.offline.${session.householdId}` },
    loadOfflineBundle(): OfflineBundle | null {
      if (!session.householdId) return null
      const raw = uni.getStorageSync(this.offlineStorageKey()) as unknown
      if (!raw) return null
      try {
        return parseOfflineBundle(typeof raw === 'string' ? JSON.parse(raw) : raw)
      } catch {
        return null
      }
    },
    localQuickAction(action: 'inventory' | 'restrictions' | 'skills', query: string) {
      const bundle = this.loadOfflineBundle()
      if (!bundle) {
        this.quickError = '请先联网保存离线包，再使用离线检索。'
        return
      }
      const hits = searchOffline(bundle, query)
      if (action === 'skills') {
        const skill = hits.skills[0]?.item
        this.quickResult = skill
          ? {
            template: 'offline',
            text: formatOfflineSkill(skill, {
              dataVersion: bundle.data_version,
              note: '离线词法检索：未联网，未执行服务端生成。',
            }),
            data_version: bundle.data_version,
            check: { line: '忌口校验：未执行：离线模式仅检索已下载知识库；结论：无法判断' },
          }
          : {
            template: 'no_result',
            text: '【离线·检索烹饪技巧】已下载知识库中没有匹配的技巧。',
            data_version: bundle.data_version,
            check: { line: '忌口校验：未执行：离线未选出可校验内容；结论：无法判断' },
          }
        return
      }
      if (!query.trim()) {
        this.quickError = action === 'inventory'
          ? '离线模式无法读取实时库存，请输入菜名或食材进行检索。'
          : '请输入菜名或食材后再检索。'
        return
      }
      const recipe = hits.recipes[0]?.item
      const note = action === 'inventory'
        ? '离线词法检索：未核对实时库存，不能据此判断食材是否够用。'
        : '离线词法检索：未执行服务端忌口校验，请核对全部食材。'
      this.quickResult = recipe
        ? {
          template: 'offline',
          text: formatOfflineRecipe(recipe, {
            dataVersion: bundle.data_version,
            note,
            disclaimer: bundle.disclaimer,
          }),
          data_version: bundle.data_version,
          check: { line: '忌口校验：未执行：离线模式未执行服务端校验；结论：无法判断' },
        }
        : {
          template: 'no_result',
          text: '【离线·菜谱检索】已下载菜谱中没有匹配结果。',
          data_version: bundle.data_version,
          check: { line: '忌口校验：未执行：离线未选出可校验菜谱；结论：无法判断' },
        }
    },
    quickAction(action: 'inventory' | 'restrictions' | 'skills') {
      if (this.quickLoading || !session.householdId) return
      const query = this.input.trim()
      if (action !== 'inventory' && !query) {
        this.quickResult = null
        this.quickError = action === 'skills'
          ? '请先输入烹饪问题或技巧关键词，再检索。'
          : '请先输入菜名或食材，再检索。'
        return
      }
      this.quickLoading = true
      this.quickError = ''
      const generation = ++this.quickGeneration
      if (this.offline) {
        this.localQuickAction(action, query)
        this.quickLoading = false
        return
      }
      return request<QuickResult>(`/households/${session.householdId}/assistant/quick-actions`, {
        action, query, offline: this.offline,
      }).then((result) => { if (generation === this.quickGeneration) this.quickResult = result }, (error: Error) => { if (generation === this.quickGeneration) this.quickError = error.message })
        .finally(() => { if (generation === this.quickGeneration) this.quickLoading = false })
    },
    closeQuickResult() {
      this.quickGeneration++
      this.quickResult = null
      this.quickError = ''
      this.quickLoading = false
    },
    downloadBundle() {
      if (!session.householdId) return
      return request<Record<string, unknown>>(`/households/${session.householdId}/offline-bundle`).then((bundle) => {
        // The device performs lexical search locally; no model or network call is allowed there.
        uni.setStorageSync(this.offlineStorageKey(), JSON.stringify(bundle))
        uni.showToast({ title: `离线包已保存 ${String(bundle.data_version || '')}`, icon: 'none' })
      }, (error: Error) => { this.quickError = error.message })
    },
    recipeSources(result: Result): Evidence[] {
      // Recipe content is rendered in the answer itself; showing a second
      // recipe source card duplicates steps and leaks traceability labels.
      const kinds = ['web', 'substitution', 'flavor_template', 'food_risk']
      return (result.evidence || []).filter((item) => kinds.includes(item.kind) && (result.citations || []).includes(item.id))
    },
    sourceLabel(kind: string): string {
      return ({ recipe: '菜谱来源', cooking_skill: '烹饪技能来源', web: '联网搜索摘要', substitution: '食材替换依据', flavor_template: '风味配比模板', food_risk: '风险规则' } as Record<string, string>)[kind] || '资料来源'
    },
    actionTitle(action: Action): string {
      if (action.type !== 'add_procurement') return '保存家庭偏好'
      const count = Array.isArray(action.payload.items) ? action.payload.items.length : 1
      return count > 1 ? `加入采购清单（${count} 项）` : '加入采购清单'
    },
    actionLines(action: Action): string[] {
      if (action.type === 'add_procurement') {
        const items = Array.isArray(action.payload.items)
          ? action.payload.items as Array<Record<string, unknown>>
          : [action.payload]
        return items.map((item) => {
          const required = typeof item.required === 'string' && item.required ? `菜谱所需 ${item.required}` : ''
          const available = typeof item.available === 'string' && item.available ? `库存 ${item.available}` : ''
          const quantity = item.quantity_status === 'unknown'
            ? '数量待核对'
            : `${String(item.quantity ?? '')} ${String(item.unit ?? '')}`.trim()
          return [String(item.name || ''), required, available, quantity].filter(Boolean).join(' · ')
        })
      }
      return [String(action.payload.preference || '请核对后保存家庭偏好')]
    },
    actionNote(action: Action): string {
      return action.type === 'add_procurement'
        ? '尚未保存；确认后加入采购清单（待购买），不会写入库存。'
        : '尚未保存；确认后保存家庭偏好。'
    },
  },
})
</script>

<template>
  <view class="assistant-page">
    <AppHeader eyebrow="" title="家庭食材助手" />
    <view class="chat-toolbar"><text class="toolbar-hint">食材、晚餐和采购，都可以聊聊</text><button :disabled="!session.householdId" @tap="showHistory">历史</button><button :disabled="loading" @tap="newChat">＋ 新对话</button></view>
    <view class="quick-bar">
      <button :disabled="quickLoading || !session.householdId" @tap="quickAction('inventory')">按库存推荐</button>
      <button :disabled="quickLoading || !session.householdId" @tap="quickAction('restrictions')">查忌口食谱</button>
      <button :disabled="quickLoading || !session.householdId" @tap="quickAction('skills')">检索烹饪技巧</button>
      <label class="offline-toggle"><switch :checked="offline" @change="toggleOffline" />离线</label>
      <button :disabled="!session.householdId" @tap="downloadBundle">保存离线包</button>
    </view>
    <view v-if="quickError" class="page-error">{{ quickError }}</view>
    <view v-if="quickResult" class="quick-result">
      <view class="quick-heading"><text>检索结果</text><button aria-label="关闭检索结果" @tap="closeQuickResult">✕ 关闭</button></view>
      <scroll-view scroll-y class="quick-body">
      <ChatContent :text="quickResult.text" />
      <text v-if="quickResult.check?.line" class="quick-meta">{{ quickResult.check.line }}</text>
      <text class="quick-meta">{{ quickResult.template === 'offline' ? '离线模板' : quickResult.template === 'web' ? '模型补充 · 未核实' : quickResult.template === 'no_result' ? '未选出可校验菜谱' : '本地确定性校验' }} · 数据版本 {{ quickResult.data_version || '未提供' }}</text>
      </scroll-view>
    </view>
    <view v-if="error" class="page-error">{{ error }}</view>
    <scroll-view class="conversation" scroll-y refresher-enabled :refresher-triggered="conversationRefreshing" :scroll-into-view="scrollTarget" :scroll-with-animation="false" @refresherrefresh="refreshConversation" @scroll="onScroll" @scrolltolower="followBottom = true">
      <view v-if="session.restored && !runs.length && !loading" class="welcome"><text class="welcome-icon">✦</text><text class="welcome-title">今天，想为家里做点什么？</text><text>查查冰箱、聊聊晚餐，或整理采购清单。</text><view class="suggestions"><button v-for="suggestion in suggestions" :key="suggestion" @tap="input = suggestion">{{ suggestion }} ↗</button></view><text class="welcome-note">采购和偏好变更会先请你确认。</text></view>
      <button v-if="hasMore" class="older" :disabled="loading" @tap="loadOlder">加载更早的消息</button>
      <text v-if="loading" class="loading">正在读取对话…</text>
      <view v-for="(run, index) in runs" :id="`message-${run.run_id || index}`" :key="run.requestKey || run.run_id" class="turn">
        <view class="user-message"><text selectable>{{ run.text }}</text></view>
        <view class="assistant-message">
          <view class="message-heading"><text class="avatar">✦</text><text>智慧助手</text><text class="status" :class="{ working: activeStatus(run.status) || run.typing }">{{ statusLabel(run) }}</text></view>
          <view v-if="run.steps.length" class="steps"><text class="steps-toggle" @tap="run.expanded = !run.expanded">{{ run.expanded ? '收起思考过程' : `思考过程 · ${run.steps.length} 条` }} {{ run.expanded ? '⌃' : '⌄' }}</text><template v-if="run.expanded"><text v-for="(step, stepIndex) in run.steps" :key="stepIndex" class="step">{{ step }}</text></template></view>
          <ChatContent v-if="run.visibleText" :text="run.visibleText" />
          <text v-if="run.typing && run.visibleText" class="typing-cursor">▍</text>
          <text v-if="!run.visibleText && (run.typing || activeStatus(run.status))" class="thinking">正在思考…</text>
          <template v-if="!run.typing">
          <view v-for="source in recipeSources(run.result)" :key="source.id" class="recipe-source">
            <text class="source-title">{{ sourceLabel(source.kind) }} · {{ source.title || source.content?.name || source.content?.title || '资料' }}</text>
            <text v-if="source.source_name || source.content?.source_name" class="source-name">{{ source.source_name || source.content?.source_name }}</text>
            <text v-if="source.notes" class="source-name">{{ source.notes }}</text>
            <text v-if="source.content?.conversion_factor" class="source-name">替换系数 {{ source.content.conversion_factor }} {{ source.content.unit }}</text>
            <text v-if="source.content?.severity" class="source-name">风险等级：{{ source.content.severity }} · 适用：{{ (source.content.applies_to || []).join('、') || '未标注' }}</text>
            <ChatContent v-if="source.content?.markdown" :text="source.content.markdown" />
            <view v-if="source.content?.steps?.length" class="source-steps">
              <text class="source-title">做法</text>
              <text v-for="(step, stepIndex) in source.content.steps" :key="stepIndex" selectable class="source-step">{{ stepIndex + 1 }}. {{ step }}</text>
            </view>
            <text v-if="source.content?.snippet" class="source-name">{{ source.content.snippet }}</text>
            <text v-if="source.source_url" class="source-name" @tap="copy(source.source_url)">{{ source.source_url }}（点击复制链接）</text>
          </view>
          </template>
          <view v-if="run === runs[runs.length - 1] && !run.typing && run.result.options?.length" class="assistant-options">
            <text class="options-title">选个方向，再展开做法</text>
            <view v-for="option in run.result.options" :key="option.key" class="assistant-option" :class="{ disabled: busy }" @tap="chooseOption(option)">
              <text class="option-label">{{ option.label }} · {{ option.example }}</text>
              <text class="option-reason">{{ option.reason }}</text>
              <text v-if="option.missing?.length" class="option-missing">缺少：{{ option.missing.join('、') }}</text>
            </view>
            <text class="options-note">只读推荐，不会改动库存或采购清单。</text>
          </view>
          <view v-if="run.result.pending_action" class="action-card"><text class="action-title">{{ actionTitle(run.result.pending_action) }}</text><text v-for="line in actionLines(run.result.pending_action)" :key="line" class="action-line">{{ line }}</text><text class="action-note">{{ actionNote(run.result.pending_action) }}</text><view class="action-buttons"><button :disabled="run.deciding" @tap="decide(run, false)">不保存</button><button class="primary" :disabled="run.deciding" @tap="decide(run, true)">{{ run.deciding ? '处理中…' : '确认并保存' }}</button></view></view>
          <text v-if="run.result.action_result" class="decision-result">{{ run.result.action_result.status === 'confirmed' ? '✓ 已确认并保存' : '已取消保存' }}</text>
          <text v-for="warning in run.result.warnings || []" :key="warning" class="warning">{{ warning }}</text>
          <text v-if="run.error || run.status === 'failed'" class="message-error">{{ run.error || '本次任务未完成。' }}</text>
          <view class="message-actions"><button v-if="run.result.text" @tap="copy(displayText(run))">复制</button><button v-if="run.typing" @tap="finishTyping(run)">显示全文</button><button v-if="run.run_id && (run.error || activeStatus(run.status))" @tap="checkRun(run)">检查状态</button><button v-if="run.status === 'failed' || run.status === 'cancelled'" :disabled="busy" @tap="retry(run)">重试</button></view>
        </view>
      </view>
      <view id="conversation-end" class="conversation-end"></view>
    </scroll-view>
    <view v-if="!followBottom && runs.length" class="jump-bottom" @tap="followBottom = true; scrollBottom(true)">↓ 回到最新消息</view>
    <view class="composer"><textarea v-model="input" class="composer-input" :maxlength="4000" auto-height :adjust-position="true" :focus="composerFocused" placeholder="给助手发消息" @blur="composerFocused = false" /><button v-if="busy" class="stop-button" :disabled="sending" @tap="cancel">停止</button><button v-else class="primary send-button" :disabled="loading || !input.trim() || !session.householdId" @tap="send">发送</button></view>
    <view v-if="historyOpen" class="history-mask" @tap="historyOpen = false"><view class="history-sheet" @tap.stop><view class="history-heading"><text>历史对话</text><button @tap="historyOpen = false">关闭</button></view><text class="history-note">仅显示当前账号在此家庭中的对话</text><scroll-view scroll-y class="history-list"><view v-for="chat in chats" :key="chat.id" class="history-row"><view class="history-item" @tap="openChat(chat.id)"><text class="history-title">{{ chat.title }}</text><text class="history-date">{{ new Date(chat.updated_at).toLocaleString() }}</text></view><button :disabled="activeStatus(chat.status)" @tap="deleteChat(chat)">删除</button></view><text v-if="!chats.length && !historyLoading" class="loading">还没有历史对话</text><button v-if="historyMore" :disabled="historyLoading" @tap="loadHistory(true)">加载更多</button><text v-if="historyLoading" class="loading">正在加载…</text></scroll-view></view></view>
  </view>
</template>

<style scoped lang="scss">
.source-step { display: block; margin-top: 12rpx; color: #3c4e65; font-size: 26rpx; line-height: 1.8; white-space: pre-wrap; }
.recipe-source { margin-top: 18rpx; padding: 18rpx 22rpx; background: #eef4fc; border-radius: 16rpx; }.source-title, .source-name { display: block; font-size: 23rpx; line-height: 1.7; color: #526780; }.source-name { color: #8793a4; font-size: 21rpx; }
.assistant-options { margin-top: 18rpx; }.options-title { display: block; margin-bottom: 10rpx; color: #304965; font-size: 24rpx; font-weight: 600; }.assistant-option { margin-top: 12rpx; padding: 18rpx 22rpx; background: #f7faff; border: 1rpx solid #dce8f8; border-radius: 18rpx; }.assistant-option.disabled { opacity: .55; }.option-label { display: block; color: #2f5f9a; font-size: 26rpx; font-weight: 600; line-height: 1.6; }.option-reason, .option-missing, .options-note { display: block; margin-top: 6rpx; color: #74859a; font-size: 22rpx; line-height: 1.6; }.option-missing { color: #9a7a4f; }.options-note { margin-top: 12rpx; color: #9aa5b5; font-size: 20rpx; }
.assistant-page { height: calc(100vh - var(--window-bottom, 0px)); display: flex; flex-direction: column; background: #f8fafc; position: relative; }
button { margin: 0; padding: 0 20rpx; border: 0; background: transparent; font-size: 24rpx; line-height: 68rpx; color: #526780; } button::after { border: 0; } button[disabled] { opacity: .45; }
.chat-toolbar { display: flex; align-items: center; padding: 4rpx 24rpx; background: white; border-bottom: 1rpx solid #edf0f5; flex-shrink: 0; }.toolbar-hint { flex: 1; color: #8793a4; font-size: 21rpx; }
.quick-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10rpx; padding: 12rpx 24rpx; background: #fff; border-bottom: 1rpx solid #edf0f5; flex-shrink: 0; }.quick-bar button { margin: 0; padding: 0 16rpx; line-height: 56rpx; border-radius: 14rpx; background: #f1f6ff; color: #3c6da8; font-size: 22rpx; }.offline-toggle { display: flex; align-items: center; gap: 2rpx; color: #7b8ba0; font-size: 21rpx; }.offline-toggle switch { transform: scale(.7); }
 .quick-result { position: relative; flex-shrink: 0; max-height: 38vh; margin: 16rpx 24rpx 0; padding: 20rpx; background: #fff; border: 1rpx solid #dbe6f5; border-radius: 20rpx; }.quick-heading { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12rpx; color: #304965; font-size: 25rpx; font-weight: 600; }.quick-heading button { padding: 0; color: #6b83a0; font-size: 22rpx; line-height: 48rpx; }.quick-body { max-height: 31vh; }.quick-meta { display: block; margin-top: 12rpx; color: #8a98aa; font-size: 21rpx; }
.conversation { flex: 1; min-height: 0; }.welcome { padding: 90rpx 38rpx 40rpx; display: flex; flex-direction: column; align-items: center; gap: 22rpx; text-align: center; color: #7a889a; font-size: 25rpx; line-height: 1.7; }.welcome-icon { color: #3b82f6; font-size: 76rpx; }.welcome-title { color: #263449; font-size: 36rpx; font-weight: 650; }.welcome-note { font-size: 22rpx; color: #94a0af; }.suggestions { display: flex; flex-direction: column; gap: 16rpx; width: 100%; margin: 25rpx 0; }.suggestions button { text-align: left; padding: 12rpx 28rpx; background: white; border: 1rpx solid #e4eaf2; border-radius: 22rpx; }
.turn { padding: 26rpx 28rpx 0; }.user-message { margin: 0 0 30rpx 70rpx; display: flex; justify-content: flex-end; }.user-message text { background: #e9f1ff; color: #24456d; border-radius: 26rpx 26rpx 6rpx 26rpx; padding: 22rpx 28rpx; font-size: 28rpx; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }.assistant-message { padding: 0 0 30rpx; border-bottom: 1rpx solid #e8edf3; }.message-heading { display: flex; align-items: center; gap: 12rpx; color: #526175; font-size: 23rpx; margin-bottom: 18rpx; }.avatar { color: #3979ec; font-size: 34rpx; }.status { margin-left: auto; color: #94a0af; font-size: 21rpx; }.status.working { color: #3979ec; }.thinking { color: #8390a2; font-size: 26rpx; line-height: 1.8; }.typing-cursor { display: inline-block; margin-left: 4rpx; color: #3979ec; font-size: 28rpx; line-height: 1; animation: cursor-blink 1s steps(1) infinite; } @keyframes cursor-blink { 50% { opacity: 0; } }.steps { background: #f0f4f9; padding: 14rpx 20rpx; border-radius: 15rpx; margin-bottom: 18rpx; }.steps-toggle { color: #6f8199; font-size: 23rpx; display: block; }.step { display: block; color: #8b98aa; font-size: 22rpx; line-height: 1.8; padding-top: 8rpx; }
.primary { background: #3478f6; border-radius: 18rpx; color: white; }.composer { flex-shrink: 0; display: flex; align-items: flex-end; gap: 18rpx; padding: 20rpx 24rpx calc(20rpx + env(safe-area-inset-bottom)); border-top: 1rpx solid #e4eaf2; background: white; }.composer-input { flex: 1; min-width: 0; min-height: 76rpx; max-height: 220rpx; box-sizing: border-box; padding: 20rpx 24rpx; line-height: 1.6; font-size: 27rpx; background: #f1f5f9; border-radius: 22rpx; }.send-button, .stop-button { line-height: 76rpx; padding: 0 26rpx; }.stop-button { border: 1rpx solid #d6dfed; border-radius: 18rpx; }
.action-card { margin-top: 20rpx; padding: 24rpx; background: white; border: 1rpx solid #dce7f9; border-radius: 22rpx; }.action-title { display: block; color: #304965; font-size: 27rpx; font-weight: 600; }.action-line { display: block; margin-top: 12rpx; color: #526780; font-size: 26rpx; }.action-note, .warning { display: block; color: #8b7959; font-size: 22rpx; margin-top: 14rpx; line-height: 1.7; }.action-buttons { display: flex; justify-content: flex-end; gap: 16rpx; margin-top: 20rpx; }.decision-result { display: block; color: #458066; font-size: 24rpx; margin-top: 18rpx; }.message-actions { display: flex; gap: 12rpx; }.message-actions button { color: #8795a6; font-size: 22rpx; padding-left: 0; }.message-error, .page-error { display: block; color: #b45f4e; font-size: 24rpx; line-height: 1.7; margin: 15rpx 0; }.page-error { padding: 0 28rpx; }.conversation-end { height: 32rpx; }.loading { display: block; text-align: center; padding: 30rpx; color: #8b98aa; font-size: 24rpx; }.older { margin: 18rpx auto 0; }.jump-bottom { position: absolute; right: 24rpx; bottom: 160rpx; background: white; box-shadow: 0 4rpx 24rpx #dce3ee; border-radius: 30rpx; padding: 16rpx 24rpx; color: #5a7ca6; font-size: 23rpx; }
.history-mask { position: fixed; inset: 0; z-index: 100; background: rgba(26, 39, 60, .3); display: flex; align-items: flex-end; }.history-sheet { width: 100%; border-radius: 32rpx 32rpx 0 0; background: #fff; padding: 28rpx 28rpx calc(28rpx + env(safe-area-inset-bottom)); box-sizing: border-box; }.history-heading { display: flex; align-items: center; justify-content: space-between; font-size: 32rpx; color: #263449; }.history-note { color: #94a0af; font-size: 22rpx; }.history-list { height: 55vh; margin-top: 24rpx; }.history-row { display: flex; align-items: center; gap: 18rpx; border-bottom: 1rpx solid #edf0f5; padding: 24rpx 0; }.history-item { flex: 1; min-width: 0; }.history-title { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #3c4e65; font-size: 27rpx; }.history-date { display: block; color: #9aa5b5; font-size: 21rpx; margin-top: 10rpx; }
</style>
