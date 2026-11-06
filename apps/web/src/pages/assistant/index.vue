<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import ChatContent from '../../components/ChatContent.vue'
import { session } from '../../shared/session'
import { streamRun, type AgentEvent } from '../../shared/stream'
import { request } from '../../shared/transport'

interface Action { id: string; type: string; payload: Record<string, unknown>; payload_hash: string; version: number }
interface Result { text?: string; pending_action?: Action; action_result?: { status: string }; warnings?: string[]; status?: string }
interface Run { run_id: string; text: string; status: string; result: Result; error_code?: string; steps: string[]; expanded: boolean; error: string; deciding: boolean; requestKey: string }
interface Chat { id: string; title: string; updated_at: string; status: string }
interface Detail { session_id: string; has_more: boolean; runs: Run[] }
const active = (status: string) => ['queued', 'running', 'cancel_requested'].includes(status)
const toolNames: Record<string, string> = { get_inventory_summary: '查询冰箱库存', get_member_constraints: '读取家庭限制', recommend_menu: '整理菜单', prepare_procurement_action: '整理采购建议', get_preference_summary: '查询家庭偏好', prepare_preference_action: '整理偏好建议', search_recipes: '搜索菜谱', get_recipe_detail: '读取菜谱', check_recipe_availability: '核对食材' }

export default defineComponent({
  name: 'AssistantPage',
  components: { AppHeader, ChatContent },
  data() {
    return {
      session, input: '', sessionId: '', runs: [] as Run[], chats: [] as Chat[],
      historyOpen: false, historyLoading: false, historyMore: false, hasMore: false,
      loading: false, sending: false, error: '', scrollTarget: '', followBottom: true, conversationRefreshing: false,
      cursor: 0, reconnects: 0, generation: 0,
      conversationHeight: 0,
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
    void this.$nextTick(() => {
      uni.createSelectorQuery().in(this).select('.conversation').boundingClientRect((rect) => {
        this.conversationHeight = (rect as { height: number }).height
      }).exec()
    })
  },
  onUnload() { this.generation++; this.stopStream?.() },
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
    reset() {
      this.generation++
      this.stopStream?.()
      this.stopStream = null
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
    decorate(run: Run): Run { return { ...run, steps: [], expanded: false, error: '', deciding: false, requestKey: '' } },
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
    send() {
      const text = this.input.trim()
      if (!text || this.busy || this.loading || !session.householdId) return
      const run: Run = { run_id: '', text, status: 'queued', result: {}, steps: [], expanded: false, error: '', deciding: false, requestKey: `${Date.now()}-${Math.random().toString(36).slice(2)}` }
      this.runs.push(run)
      this.input = ''
      this.followBottom = true
      // Use Vue's reactive row so asynchronous events update the rendered message.
      void this.submit(this.runs[this.runs.length - 1])
    },
    submit(run: Run) {
      this.sending = true
      run.error = ''
      run.status = 'queued'
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
        }
        if (typeof event.payload.delta === 'string') run.result.text = (run.result.text || '') + event.payload.delta
        if (event.type === 'action_required') run.result.pending_action = event.payload as unknown as Action
        if (['completed', 'failed', 'cancelled'].includes(event.type)) {
          run.result = event.payload as Result
          run.status = String(event.payload.status || event.type)
          if (run.status === 'failed') run.error = run.result.text || '本次任务未完成，请检查状态或重试。'
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
    actionLines(action: Action): string[] {
      if (action.type === 'add_procurement') return [String(action.payload.name), action.payload.quantity_status === 'unknown' ? '数量待核对' : `${action.payload.quantity} ${action.payload.unit}`]
      return [String(action.payload.preference || '请核对后保存家庭偏好')]
    },
  },
})
</script>

<template>
  <view class="assistant-page">
    <AppHeader eyebrow="" title="家庭食材助手" />
    <view class="chat-toolbar"><text class="toolbar-hint">食材、晚餐和采购，都可以聊聊</text><button :disabled="!session.householdId" @tap="showHistory">历史</button><button :disabled="loading" @tap="newChat">＋ 新对话</button></view>
    <view v-if="error" class="page-error">{{ error }}</view>
    <scroll-view class="conversation" scroll-y refresher-enabled :refresher-triggered="conversationRefreshing" :scroll-into-view="scrollTarget" :scroll-with-animation="false" @refresherrefresh="refreshConversation" @scroll="onScroll" @scrolltolower="followBottom = true">
      <view v-if="session.restored && !runs.length && !loading" class="welcome"><text class="welcome-icon">✦</text><text class="welcome-title">今天，想为家里做点什么？</text><text>查查冰箱、聊聊晚餐，或整理采购清单。</text><view class="suggestions"><button v-for="suggestion in suggestions" :key="suggestion" @tap="input = suggestion">{{ suggestion }} ↗</button></view><text class="welcome-note">采购和偏好变更会先请你确认。</text></view>
      <button v-if="hasMore" class="older" :disabled="loading" @tap="loadOlder">加载更早的消息</button>
      <text v-if="loading" class="loading">正在读取对话…</text>
      <view v-for="(run, index) in runs" :id="`message-${run.run_id || index}`" :key="run.requestKey || run.run_id" class="turn">
        <view class="user-message"><text selectable>{{ run.text }}</text></view>
        <view class="assistant-message">
          <view class="message-heading"><text class="avatar">✦</text><text>智慧助手</text><text class="status" :class="{ working: activeStatus(run.status) }">{{ label(run.status) }}</text></view>
          <view v-if="run.steps.length" class="steps"><text class="steps-toggle" @tap="run.expanded = !run.expanded">{{ run.expanded ? '收起执行过程' : `已处理 ${Math.ceil(run.steps.length / 2)} 个步骤` }} {{ run.expanded ? '⌃' : '⌄' }}</text><template v-if="run.expanded"><text v-for="(step, stepIndex) in run.steps" :key="stepIndex" class="step">{{ step }}</text></template></view>
          <ChatContent v-if="run.result.text" :text="run.result.text" />
          <text v-else-if="activeStatus(run.status)" class="thinking">正在为你整理回答…</text>
          <view v-if="run.result.pending_action" class="action-card"><text class="action-title">{{ run.result.pending_action.type === 'add_procurement' ? '加入采购清单' : '保存家庭偏好' }}</text><text v-for="line in actionLines(run.result.pending_action)" :key="line" class="action-line">{{ line }}</text><text class="action-note">尚未保存，确认后执行。</text><view class="action-buttons"><button :disabled="run.deciding" @tap="decide(run, false)">不保存</button><button class="primary" :disabled="run.deciding" @tap="decide(run, true)">{{ run.deciding ? '处理中…' : '确认并保存' }}</button></view></view>
          <text v-if="run.result.action_result" class="decision-result">{{ run.result.action_result.status === 'confirmed' ? '✓ 已确认并保存' : '已取消保存' }}</text>
          <text v-for="warning in run.result.warnings || []" :key="warning" class="warning">{{ warning }}</text>
          <text v-if="run.error || run.status === 'failed'" class="message-error">{{ run.error || '本次任务未完成。' }}</text>
          <view class="message-actions"><button v-if="run.result.text" @tap="copy(run.result.text)">复制</button><button v-if="run.run_id && (run.error || activeStatus(run.status))" @tap="checkRun(run)">检查状态</button><button v-if="run.status === 'failed' || run.status === 'cancelled'" :disabled="busy" @tap="retry(run)">重试</button></view>
        </view>
      </view>
      <view id="conversation-end" class="conversation-end"></view>
    </scroll-view>
    <view v-if="!followBottom && runs.length" class="jump-bottom" @tap="followBottom = true; scrollBottom(true)">↓ 回到最新消息</view>
    <view class="composer"><textarea v-model="input" class="composer-input" :maxlength="4000" auto-height :adjust-position="true" placeholder="给助手发消息" /><button v-if="busy" class="stop-button" :disabled="sending" @tap="cancel">停止</button><button v-else class="primary send-button" :disabled="loading || !input.trim() || !session.householdId" @tap="send">发送</button></view>
    <view v-if="historyOpen" class="history-mask" @tap="historyOpen = false"><view class="history-sheet" @tap.stop><view class="history-heading"><text>历史对话</text><button @tap="historyOpen = false">关闭</button></view><text class="history-note">仅显示当前账号在此家庭中的对话</text><scroll-view scroll-y class="history-list"><view v-for="chat in chats" :key="chat.id" class="history-row"><view class="history-item" @tap="openChat(chat.id)"><text class="history-title">{{ chat.title }}</text><text class="history-date">{{ new Date(chat.updated_at).toLocaleString() }}</text></view><button :disabled="activeStatus(chat.status)" @tap="deleteChat(chat)">删除</button></view><text v-if="!chats.length && !historyLoading" class="loading">还没有历史对话</text><button v-if="historyMore" :disabled="historyLoading" @tap="loadHistory(true)">加载更多</button><text v-if="historyLoading" class="loading">正在加载…</text></scroll-view></view></view>
  </view>
</template>

<style scoped lang="scss">
.assistant-page { height: calc(100vh - var(--window-bottom, 0px)); display: flex; flex-direction: column; background: #f8fafc; position: relative; }
button { margin: 0; padding: 0 20rpx; border: 0; background: transparent; font-size: 24rpx; line-height: 68rpx; color: #526780; } button::after { border: 0; } button[disabled] { opacity: .45; }
.chat-toolbar { display: flex; align-items: center; padding: 4rpx 24rpx; background: white; border-bottom: 1rpx solid #edf0f5; flex-shrink: 0; }.toolbar-hint { flex: 1; color: #8793a4; font-size: 21rpx; }
.conversation { flex: 1; min-height: 0; }.welcome { padding: 90rpx 38rpx 40rpx; display: flex; flex-direction: column; align-items: center; gap: 22rpx; text-align: center; color: #7a889a; font-size: 25rpx; line-height: 1.7; }.welcome-icon { color: #3b82f6; font-size: 76rpx; }.welcome-title { color: #263449; font-size: 36rpx; font-weight: 650; }.welcome-note { font-size: 22rpx; color: #94a0af; }.suggestions { display: flex; flex-direction: column; gap: 16rpx; width: 100%; margin: 25rpx 0; }.suggestions button { text-align: left; padding: 12rpx 28rpx; background: white; border: 1rpx solid #e4eaf2; border-radius: 22rpx; }
.turn { padding: 26rpx 28rpx 0; }.user-message { margin: 0 0 30rpx 70rpx; display: flex; justify-content: flex-end; }.user-message text { background: #e9f1ff; color: #24456d; border-radius: 26rpx 26rpx 6rpx 26rpx; padding: 22rpx 28rpx; font-size: 28rpx; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }.assistant-message { padding: 0 0 30rpx; border-bottom: 1rpx solid #e8edf3; }.message-heading { display: flex; align-items: center; gap: 12rpx; color: #526175; font-size: 23rpx; margin-bottom: 18rpx; }.avatar { color: #3979ec; font-size: 34rpx; }.status { margin-left: auto; color: #94a0af; font-size: 21rpx; }.status.working { color: #3979ec; }.thinking { color: #8390a2; font-size: 26rpx; line-height: 1.8; }.steps { background: #f0f4f9; padding: 14rpx 20rpx; border-radius: 15rpx; margin-bottom: 18rpx; }.steps-toggle { color: #6f8199; font-size: 23rpx; display: block; }.step { display: block; color: #8b98aa; font-size: 22rpx; line-height: 1.8; padding-top: 8rpx; }
.primary { background: #3478f6; border-radius: 18rpx; color: white; }.composer { flex-shrink: 0; display: flex; align-items: flex-end; gap: 18rpx; padding: 20rpx 24rpx calc(20rpx + env(safe-area-inset-bottom)); border-top: 1rpx solid #e4eaf2; background: white; }.composer-input { flex: 1; min-width: 0; min-height: 76rpx; max-height: 220rpx; box-sizing: border-box; padding: 20rpx 24rpx; line-height: 1.6; font-size: 27rpx; background: #f1f5f9; border-radius: 22rpx; }.send-button, .stop-button { line-height: 76rpx; padding: 0 26rpx; }.stop-button { border: 1rpx solid #d6dfed; border-radius: 18rpx; }
.action-card { margin-top: 20rpx; padding: 24rpx; background: white; border: 1rpx solid #dce7f9; border-radius: 22rpx; }.action-title { display: block; color: #304965; font-size: 27rpx; font-weight: 600; }.action-line { display: block; margin-top: 12rpx; color: #526780; font-size: 26rpx; }.action-note, .warning { display: block; color: #8b7959; font-size: 22rpx; margin-top: 14rpx; line-height: 1.7; }.action-buttons { display: flex; justify-content: flex-end; gap: 16rpx; margin-top: 20rpx; }.decision-result { display: block; color: #458066; font-size: 24rpx; margin-top: 18rpx; }.message-actions { display: flex; gap: 12rpx; }.message-actions button { color: #8795a6; font-size: 22rpx; padding-left: 0; }.message-error, .page-error { display: block; color: #b45f4e; font-size: 24rpx; line-height: 1.7; margin: 15rpx 0; }.page-error { padding: 0 28rpx; }.conversation-end { height: 32rpx; }.loading { display: block; text-align: center; padding: 30rpx; color: #8b98aa; font-size: 24rpx; }.older { margin: 18rpx auto 0; }.jump-bottom { position: absolute; right: 24rpx; bottom: 160rpx; background: white; box-shadow: 0 4rpx 24rpx #dce3ee; border-radius: 30rpx; padding: 16rpx 24rpx; color: #5a7ca6; font-size: 23rpx; }
.history-mask { position: fixed; inset: 0; z-index: 100; background: rgba(26, 39, 60, .3); display: flex; align-items: flex-end; }.history-sheet { width: 100%; border-radius: 32rpx 32rpx 0 0; background: #fff; padding: 28rpx 28rpx calc(28rpx + env(safe-area-inset-bottom)); box-sizing: border-box; }.history-heading { display: flex; align-items: center; justify-content: space-between; font-size: 32rpx; color: #263449; }.history-note { color: #94a0af; font-size: 22rpx; }.history-list { height: 55vh; margin-top: 24rpx; }.history-row { display: flex; align-items: center; gap: 18rpx; border-bottom: 1rpx solid #edf0f5; padding: 24rpx 0; }.history-item { flex: 1; min-width: 0; }.history-title { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #3c4e65; font-size: 27rpx; }.history-date { display: block; color: #9aa5b5; font-size: 21rpx; margin-top: 10rpx; }
</style>
