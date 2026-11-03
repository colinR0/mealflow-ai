<script lang="ts">
import { defineComponent } from 'vue'

interface Evidence {
  id: string
  kind?: string
  name?: string
  source_id?: string
  source_url?: string
  content_version?: number
  content?: Record<string, unknown>
  excerpt?: string
  quantity_status?: string
  quantity?: string | null
  unit?: string
  confirmed_at?: string
}
interface RunResult {
  text?: string
  fake?: boolean
  error_code?: string
  warnings?: string[]
  evidence?: Evidence[]
  menu_draft?: Evidence[]
  citations?: string[]
  pending_action?: {
    id: string
    type: string
    payload: Record<string, unknown>
    version: number
    payload_hash: string
  }
}
const storageKey = 'mealflow.durable.ids.v1'
const activeStatuses = ['queued', 'running']

export default defineComponent({
  name: 'DurableWorkbench',
  data() {
    return {
      email: '',
      password: '',
      csrf: '',
      userId: '',
      householdId: '',
      households: [] as { id: string; name: string }[],
      text: '',
      sessionId: '',
      runId: '',
      status: '',
      error: '',
      notice: '',
      result: {} as RunResult,
      source: null as EventSource | null,
      lastSeq: 0,
      busy: false,
      epoch: 0,
      decisionMessage: '',
    }
  },
  computed: {
    running(): boolean {
      return activeStatuses.includes(this.status)
    },
    statusLabel(): string {
      return (
        (
          {
            queued: '等待处理',
            running: '处理中',
            succeeded: '已完成',
            completed: '已完成',
            failed: '未完成',
            cancelled: '已取消',
            waiting_input: '请补充信息',
            waiting_confirmation: '等待确认',
          } as Record<string, string>
        )[this.status] ||
        this.status ||
        '尚未开始'
      )
    },
    recipeCards(): Evidence[] {
      const rows = [
        ...(this.result.menu_draft || []),
        ...(this.result.evidence || []).filter((item) => item.kind === 'recipe'),
      ]
      return [...new Map(rows.map((item) => [item.id, item])).values()]
    },
    otherEvidence(): Evidence[] {
      return (this.result.evidence || []).filter((item) => item.kind !== 'recipe')
    },
  },
  mounted() {
    try {
      if (localStorage.getItem(storageKey)) this.notice = '登录后将恢复上次会话和结果。'
    } catch {
      this.notice = '浏览器无法保存会话位置；本次仍可正常使用。'
    }
  },
  beforeUnmount() {
    this.epoch++
    this.closeStream()
  },
  methods: {
    closeStream() {
      this.source?.close()
      this.source = null
    },
    clearSession() {
      this.epoch++
      this.closeStream()
      this.sessionId = ''
      this.runId = ''
      this.status = ''
      this.text = ''
      this.result = {}
      this.lastSeq = 0
      this.error = ''
      this.decisionMessage = ''
    },
    newSession() {
      this.clearSession()
      this.notice = '已开始新会话，可以重新选择家庭。'
      try {
        localStorage.removeItem(storageKey)
      } catch {
        /* Storage may be disabled. */
      }
    },
    saveIds() {
      try {
        localStorage.setItem(
          storageKey,
          JSON.stringify({
            userId: this.userId,
            householdId: this.householdId,
            sessionId: this.sessionId,
            runId: this.runId,
          }),
        )
      } catch {
        this.notice = '浏览器无法保存会话位置，刷新后需重新开始。'
      }
    },
    signedOut() {
      this.clearSession()
      this.csrf = ''
      this.password = ''
      this.userId = ''
      this.households = []
      this.householdId = ''
    },
    async api(path: string, body?: unknown, key?: string) {
      const response = await fetch(`/api/v2${path}`, {
        method: body === undefined ? 'GET' : 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': this.csrf,
          ...(key ? { 'Idempotency-Key': key } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const value = await response.json().catch(() => null)
      if (!response.ok) {
        const code = value?.detail?.code || `HTTP ${response.status}`
        if (response.status === 401 || code === 'CSRF_REJECTED') {
          this.signedOut()
          throw new Error('登录已失效，请重新登录以恢复结果。')
        }
        const messages: Record<string, string> = {
          INVALID_CREDENTIALS: '邮箱或密码不正确。',
          ACTION_STALE: '家庭信息已变化，请重新发送请求后确认。',
          ACTION_EXPIRED: '该操作已过期，请重新发送请求。',
          ACTION_ALREADY_DECIDED: '该操作已处理，请恢复结果核对。',
          RUN_NOT_FOUND: '找不到该结果，或当前账号无权查看。',
        }
        throw new Error(messages[code] || `请求未完成（${code}），请重试。`)
      }
      return value
    },
    showError(error: unknown) {
      this.error = error instanceof Error ? error.message : '请求未完成，请重试。'
    },
    async login() {
      if (this.busy) return
      this.busy = true
      this.error = ''
      this.notice = ''
      try {
        const value = await this.api('/auth/login', { email: this.email, password: this.password })
        this.csrf = value.csrf_token
        this.userId = value.user_id
        this.password = ''
        this.households = await this.api('/households')
        this.householdId = this.households[0]?.id || ''
        try {
          const saved = JSON.parse(localStorage.getItem(storageKey) || 'null')
          if (
            saved?.userId === this.userId &&
            this.households.some((item) => item.id === saved.householdId) &&
            typeof saved.runId === 'string' &&
            typeof saved.sessionId === 'string'
          ) {
            this.householdId = saved.householdId
            this.runId = saved.runId
            this.sessionId = saved.sessionId
          }
        } catch {
          this.notice = '未能读取上次会话，可以开始新会话。'
        }
        if (this.runId) await this.restore()
      } catch (error) {
        this.showError(error)
      } finally {
        this.password = ''
        this.busy = false
      }
    },
    async logout() {
      if (this.busy) return
      this.busy = true
      this.error = ''
      this.closeStream()
      try {
        await this.api('/auth/logout', {})
        this.signedOut()
        this.notice = '已退出。重新登录后可恢复上次结果。'
      } catch (error) {
        this.showError(error)
        if (this.csrf && this.running) this.watchRun()
      } finally {
        this.busy = false
      }
    },
    async send() {
      if (this.busy || this.running || !this.text.trim()) return
      this.busy = true
      this.error = ''
      this.notice = ''
      try {
        const run = await this.api(
          '/agent/turn-runs',
          {
            household_id: this.householdId,
            session_id: this.sessionId || null,
            parent_run_id: this.runId || null,
            text: this.text.trim(),
          },
          crypto.randomUUID(),
        )
        this.closeStream()
        this.epoch++
        this.sessionId = run.session_id
        this.runId = run.run_id
        this.status = run.status
        this.result = {}
        this.lastSeq = 0
        this.decisionMessage = ''
        this.text = ''
        this.saveIds()
        this.watchRun()
      } catch (error) {
        this.showError(error)
      } finally {
        this.busy = false
      }
    },
    watchRun() {
      this.closeStream()
      const epoch = this.epoch
      const source = new EventSource(
        `/api/v2/agent/turn-runs/${encodeURIComponent(this.runId)}/events`,
        { withCredentials: true },
      )
      this.source = source
      for (const kind of ['status', 'completed', 'failed', 'cancelled']) {
        source.addEventListener(kind, (event) => {
          if (this.source !== source || epoch !== this.epoch) return
          try {
            const value = JSON.parse((event as MessageEvent).data)
            if (value.seq <= this.lastSeq) return
            this.lastSeq = value.seq
            this.status = value.payload.status
            this.error = ''
            if (kind !== 'status') {
              this.result = value.payload || {}
              this.closeStream()
            }
          } catch {
            this.error = '结果暂时无法显示，请点击“恢复结果”重试。'
          }
        })
      }
      source.onerror = () => {
        if (this.source !== source || epoch !== this.epoch) return
        this.closeStream()
        // Read the authoritative snapshot, including authorization failures, before reconnecting.
        void this.restore(false)
      }
    },
    async restore(reconnect = true) {
      if (!this.runId || !this.csrf) return
      const epoch = this.epoch
      this.error = ''
      try {
        const run = await this.api(`/agent/turn-runs/${encodeURIComponent(this.runId)}`)
        if (epoch !== this.epoch) return
        this.status = run.status
        this.result = run.result || {}
        this.sessionId = run.session_id
        this.saveIds()
        if (this.running && reconnect) this.watchRun()
        else if (!this.running) this.closeStream()
        else this.error = '实时连接已断开，任务仍在处理。请点击“恢复结果”查看进展。'
      } catch (error) {
        if (epoch === this.epoch || !this.csrf) this.showError(error)
      }
    },
    async cancel() {
      if (this.busy) return
      this.busy = true
      this.error = ''
      try {
        await this.api(`/agent/turn-runs/${encodeURIComponent(this.runId)}/cancel`, {})
        this.notice = '已请求取消，正在确认处理结果。'
        await this.restore()
      } catch (error) {
        this.showError(error)
      } finally {
        this.busy = false
      }
    },
    async decide(accept: boolean) {
      const action = this.result.pending_action
      if (!action || this.busy || this.decisionMessage) return
      this.busy = true
      this.error = ''
      try {
        const value = await this.api(
          `/actions/${encodeURIComponent(action.id)}/${accept ? 'confirm' : 'reject'}`,
          {
            expected_version: action.version,
            payload_hash: action.payload_hash,
          },
          `${action.id}:${accept}`,
        )
        this.decisionMessage = `操作结果：${value.status === 'confirmed' ? '已确认' : value.status === 'rejected' ? '已拒绝' : value.status}`
      } catch (error) {
        this.showError(error)
      } finally {
        this.busy = false
      }
    },
    format(value: unknown): string {
      if (value == null) return ''
      return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
    },
    title(item: Evidence): string {
      return item.name || this.format(item.content?.name) || '菜谱'
    },
    steps(item: Evidence): string[] {
      const steps = item.content?.steps
      return Array.isArray(steps) ? steps.map(this.format) : steps ? [this.format(steps)] : []
    },
    sourceUrl(item: Evidence): string | undefined {
      const url = item.source_url || this.format(item.content?.source_url)
      return /^https?:\/\//i.test(url) ? url : undefined
    },
    evidenceTitle(id: string): string {
      const item = [...this.recipeCards, ...this.otherEvidence].find((row) => row.id === id)
      return item
        ? `${item.kind === 'inventory' ? item.name : this.title(item)}（${id}）`
        : `${id}（未返回对应依据）`
    },
  },
})
</script>

<template>
  <main class="durable-workbench">
    <h2>菜单与食材助手 · 联调入口</h2>
    <p class="muted">本地 fake 模型演示，未调用真实模型。</p>
    <form v-if="!csrf" @submit.prevent="login">
      <label>邮箱<input v-model="email" type="email" autocomplete="username" required /></label>
      <label
        >密码<input v-model="password" type="password" autocomplete="current-password" required
      /></label>
      <button :disabled="busy">{{ busy ? '登录中…' : '登录' }}</button>
    </form>
    <template v-else>
      <div class="toolbar">
        <label
          >家庭
          <select v-model="householdId" :disabled="!!sessionId || busy">
            <option v-for="item in households" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select></label
        >
        <button :disabled="busy" @click="newSession">新会话</button>
        <button :disabled="busy" @click="logout">退出登录</button>
      </div>
      <p v-if="!households.length">当前账号还没有可用家庭，请先加入家庭。</p>
      <form @submit.prevent="send">
        <label
          >想吃什么或需要买什么？<textarea
            v-model="text"
            maxlength="4000"
            placeholder="例如：2 人晚餐，推荐三道菜；或：买点鸡蛋"
            required
          />
        </label>
        <button :disabled="!householdId || running || busy || !text.trim()">发送</button>
      </form>
      <div class="toolbar">
        <span role="status">处理状态：{{ statusLabel }}</span>
        <button v-if="runId" :disabled="busy" @click="restore()">恢复结果</button>
        <button v-if="running" :disabled="busy" @click="cancel">取消</button>
      </div>
      <p class="answer">{{ result.text }}</p>
      <p v-if="result.error_code" role="alert">
        本次未能完成（{{ result.error_code }}），请重试或开始新会话。
      </p>
      <ul v-if="result.warnings?.length" class="warnings">
        <li v-for="(warning, index) in result.warnings" :key="index">{{ warning }}</li>
      </ul>
      <section v-if="recipeCards.length" aria-label="菜单及菜谱依据">
        <h3>{{ result.menu_draft?.length ? '本次菜单（待核对）' : '菜谱依据' }}</h3>
        <p>库存量未知或尚未核对用量时，无法判断食材是否足够，请在做饭前确认。</p>
        <article
          v-for="item in recipeCards"
          :id="`evidence-${item.id}`"
          :key="item.id"
          class="evidence-card"
        >
          <h4>{{ title(item) }}</h4>
          <p v-if="result.menu_draft?.some((dish) => dish.id === item.id)" class="muted">
            本次菜单
          </p>
          <p v-if="item.content?.ingredients">食材：{{ format(item.content.ingredients) }}</p>
          <h5>原文步骤</h5>
          <ol v-if="steps(item).length">
            <li v-for="(step, index) in steps(item)" :key="index" class="original">{{ step }}</li>
          </ol>
          <p v-else>本次未返回原文步骤，请核对来源后操作。</p>
          <p v-if="item.excerpt" class="original">原文摘录：{{ item.excerpt }}</p>
          <p>
            出处：<a
              v-if="sourceUrl(item)"
              :href="sourceUrl(item)"
              target="_blank"
              rel="noopener noreferrer"
              >{{ item.source_id || '查看来源' }}</a
            ><span v-else>{{ item.source_id || '未提供' }}</span> · 版本：{{
              item.content_version ?? '未提供'
            }}
          </p>
          <small>依据编号：{{ item.id }}</small>
        </article>
      </section>
      <section v-if="otherEvidence.length" aria-label="食材及其他依据">
        <h3>食材及其他依据</h3>
        <article
          v-for="item in otherEvidence"
          :id="`evidence-${item.id}`"
          :key="item.id"
          class="evidence-card"
        >
          <h4>{{ item.name || '参考依据' }}</h4>
          <template v-if="item.kind === 'inventory'">
            <p v-if="item.quantity_status === 'unknown' || item.quantity == null">
              库存量未知，请确认实际剩余量；不能据此判断足量。
            </p>
            <p v-else>记录数量：{{ item.quantity }} {{ item.unit }}；是否够用仍需核对菜谱用量。</p>
            <p>记录时间：{{ item.confirmed_at || '未提供' }}</p>
            <p>出处：家庭库存记录</p>
          </template>
          <p v-else class="original">{{ item.excerpt || format(item.content) }}</p>
          <small>依据及版本编号：{{ item.id }}</small>
        </article>
      </section>
      <section v-if="result.citations?.length">
        <h3>回答引用</h3>
        <ul>
          <li v-for="id in result.citations" :key="id">
            <a :href="`#evidence-${id}`">{{ evidenceTitle(id) }}</a>
          </li>
        </ul>
      </section>
      <section v-if="result.pending_action" class="evidence-card">
        <h3>请核对待确认操作</h3>
        <p>
          {{
            result.pending_action.type === 'add_procurement'
              ? '加入采购清单'
              : result.pending_action.type
          }}
        </p>
        <p v-if="result.pending_action.payload.name">
          食材：{{ result.pending_action.payload.name }}
        </p>
        <p v-if="result.pending_action.payload.quantity_status === 'unknown'">
          采购量尚未确定，请按实际需要补充。
        </p>
        <details>
          <summary>查看操作详情</summary>
          <pre>{{ JSON.stringify(result.pending_action.payload, null, 2) }}</pre>
        </details>
        <p v-if="decisionMessage" role="status">{{ decisionMessage }}</p>
        <template v-else>
          <button :disabled="busy" @click="decide(true)">确认提交</button>
          <button :disabled="busy" @click="decide(false)">拒绝</button>
        </template>
      </section>
    </template>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </main>
</template>

<style scoped>
.durable-workbench {
  max-width: 860px;
  margin: 24px auto;
  padding: 24px;
  overflow-wrap: anywhere;
}
form,
label {
  display: grid;
  gap: 8px;
}
form {
  gap: 12px;
  margin: 16px 0;
}
input,
select,
textarea,
button {
  padding: 10px;
  font: inherit;
  max-width: 100%;
  box-sizing: border-box;
}
textarea {
  min-height: 100px;
  width: 100%;
}
button {
  cursor: pointer;
}
button:disabled {
  cursor: default;
  opacity: 0.6;
}
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.toolbar label {
  display: flex;
  align-items: center;
}
.evidence-card {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 16px;
  margin: 12px 0;
  scroll-margin-top: 24px;
}
h4 {
  margin-top: 0;
  font-size: 1.1rem;
}
h5 {
  font-size: 1rem;
  margin-bottom: 8px;
}
.answer,
.original,
pre {
  white-space: pre-wrap;
}
pre {
  overflow-wrap: anywhere;
}
.muted,
small {
  color: #475569;
}
.warnings {
  padding: 16px 32px;
  background: #fff7ed;
  color: #9a3412;
}
[role='alert'] {
  color: #b42318;
}
a {
  color: #1d4ed8;
}
</style>
