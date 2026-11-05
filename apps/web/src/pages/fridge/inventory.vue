<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'
import { session } from '../../shared/session'
import { request } from '../../shared/transport'
import type { InventoryLot, InventoryPage } from '../../shared/types'

export default defineComponent({
  name: 'FridgeInventoryPage',
  components: { SubpageHeader, BottomNav },
  data() { return {
    session, items: [] as InventoryLot[], total: 0, offset: 0, limit: 30, hasMore: false,
    search: '', filter: 'all', status: 'active', loading: false, busyId: '', error: '',
    errorRetry: 'page' as 'page' | 'refresh',
    searchTimer: undefined as ReturnType<typeof setTimeout> | undefined, generation: 0,
    editing: null as InventoryLot | null, editName: '', editStatus: 'unknown' as 'known' | 'unknown',
    editQuantity: '', editUnit: '', editNotes: '', editError: '',
  } },
  computed: {
    editCanSave(): boolean {
      return Boolean(this.editName.trim()) && (this.editStatus === 'unknown' || (
        Boolean(this.editQuantity.trim()) && Number.isFinite(Number(this.editQuantity)) &&
        Number(this.editQuantity) >= 0 && Boolean(this.editUnit.trim())
      ))
    },
  },
  onShow() { void this.refresh() },
  onUnload() { clearTimeout(this.searchTimer); this.generation++ },
  onPullDownRefresh() { void this.refresh().finally(() => uni.stopPullDownRefresh()) },
  watch: {
    'session.householdId'() { this.editing = null; void this.refresh() },
    filter() { void this.refresh() },
    status() { void this.refresh() },
    search() {
      clearTimeout(this.searchTimer)
      this.reset()
      this.searchTimer = setTimeout(() => void this.load(), 250)
    },
  },
  methods: {
    reset() {
      // Invalidate in-flight pages immediately when the household or query changes.
      this.generation++; this.loading = false; this.offset = 0
      this.items = []; this.total = 0; this.hasMore = false; this.error = ''
    },
    async refresh() {
      clearTimeout(this.searchTimer)
      this.reset()
      await this.load()
    },
    async load() {
      if (!session.householdId || this.loading) return
      const generation = this.generation
      this.loading = true; this.error = ''
      try {
        const quantityFilter = this.filter === 'unknown' ? '&quantity_status=unknown' : ''
        const query = `/households/${session.householdId}/inventory-lots?offset=${this.offset}&limit=${this.limit}&status=${this.status}${quantityFilter}&search=${encodeURIComponent(this.search.trim())}`
        const response = await request<InventoryPage | InventoryLot[]>(query)
        if (generation !== this.generation) return
        if (Array.isArray(response)) {
          this.items = this.items.concat(response)
          this.total = response.length; this.offset = response.length; this.hasMore = false
        } else if (Array.isArray(response.items)) {
          this.items = this.items.concat(response.items)
          this.total = response.total; this.offset = response.offset + response.items.length; this.hasMore = response.has_more
        } else {
          throw new Error('库存接口响应格式无效，请确认后端服务已更新')
        }
      } catch (error) {
        if (generation === this.generation) {
          this.error = error instanceof Error ? error.message : '加载失败'
          this.errorRetry = 'page'
        }
      } finally { if (generation === this.generation) this.loading = false }
    },
    openReview() { uni.navigateTo({ url: '/pages/fridge/review' }) },
    openManualAdd() { uni.navigateTo({ url: '/pages/fridge/manual-add' }) },
    edit(item: InventoryLot) {
      if (this.busyId) return
      this.editing = item; this.editName = item.name; this.editStatus = item.quantity_status
      this.editQuantity = item.quantity ?? ''; this.editUnit = item.unit ?? ''
      this.editNotes = item.notes; this.editError = ''
    },
    async saveEdit() {
      if (!this.editing || this.busyId) return
      if (!this.editName.trim()) { this.editError = '请填写食材名称'; return }
      if (!this.editCanSave) { this.editError = '请填写非负数量和单位'; return }
      const item = this.editing; const householdId = session.householdId
      const payload = {
        expected_version: item.version, name: this.editName.trim(), quantity_status: this.editStatus,
        quantity: this.editStatus === 'known' ? this.editQuantity : null,
        unit: this.editStatus === 'known' ? this.editUnit.trim() : null, notes: this.editNotes.trim(),
      }
      this.busyId = item.id; this.editError = ''
      try {
        await request(`/households/${householdId}/inventory-lots/${item.id}`, payload, true, {}, 'PATCH')
        this.editing = null
        await this.refresh()
      } catch (error) {
        this.editError = error instanceof Error ? error.message : '保存失败'
        this.error = this.editError; this.errorRetry = 'refresh'
        if (householdId !== session.householdId) this.editing = null
      } finally { this.busyId = '' }
    },
    async transition(item: InventoryLot, action: 'consume' | 'remove') {
      if (this.busyId) return
      const householdId = session.householdId
      this.busyId = item.id; this.error = ''
      try {
        const confirmed = await new Promise<boolean>((resolve, reject) => uni.showModal({
          title: action === 'consume' ? '标记用完' : '移除库存',
          content: action === 'consume' ? `确认将“${item.name}”标记为用完？` : `确认移除“${item.name}”？`,
          success: (res) => resolve(Boolean(res.confirm)), fail: reject,
        }))
        if (!confirmed || householdId !== session.householdId) return
        await request(`/households/${householdId}/inventory-lots/${item.id}${action === 'consume' ? '/consume' : ''}`,
          { expected_version: item.version }, true, {}, action === 'consume' ? 'POST' : 'DELETE')
        await this.refresh()
      } catch (error) {
        this.error = error instanceof Error ? error.message : '操作失败，请重试'
        // A lost response may already have committed: reload versions before another write.
        this.errorRetry = 'refresh'
      } finally { this.busyId = '' }
    },
    retryError() {
      if (this.errorRetry === 'refresh') { this.editing = null; return this.refresh() }
      return this.load()
    },
  },
})
</script>

<template>
  <view class="secondary-page"><SubpageHeader title="冰箱库存" /><scroll-view scroll-y class="secondary-scroll">
    <view class="search-box"><text>⌕</text><input v-model="search" placeholder="搜索食材" /></view>
    <view class="filter-tabs">
      <text :class="{ selected: status === 'active' }" @tap="status = 'active'">在库</text>
      <text :class="{ selected: status === 'consumed' }" @tap="status = 'consumed'">已用完</text>
      <text :class="{ selected: status === 'removed' }" @tap="status = 'removed'">已移除</text>
      <text :class="{ selected: status === 'all' }" @tap="status = 'all'">全部</text>
    </view>
    <view class="filter-tabs"><text :class="{ selected: filter === 'all' }" @tap="filter = 'all'">全部数量</text><text :class="{ selected: filter === 'unknown' }" @tap="filter = 'unknown'">数量未知</text></view>
    <view v-if="error" class="error-copy">{{ error }}<button :disabled="loading || Boolean(busyId)" @tap="retryError()">{{ errorRetry === 'refresh' ? '刷新库存' : '重试本页' }}</button></view>
    <view class="list-panel">
      <text class="panel-title">已确认食材 · 匹配 {{ total }} 条</text>
      <view v-if="loading && !items.length" class="state-copy">正在读取库存…</view>
      <view v-else-if="!items.length" class="state-copy">没有符合条件的记录。</view>
      <view v-for="item in items" :key="item.id" class="inventory-row">
        <view><text class="food-name">{{ item.name }}</text><text v-if="item.quantity_status === 'unknown'" class="unknown-tag">数量未知 / 待核对</text><text v-else class="quantity">{{ item.quantity }} {{ item.unit }}</text><text v-if="item.status !== 'active'">{{ item.status === 'consumed' ? '已用完' : '已移除' }}</text></view>
        <view class="row-actions">
          <button :disabled="Boolean(busyId)" @tap="edit(item)">编辑</button>
          <button v-if="item.status === 'active'" :disabled="Boolean(busyId)" @tap="transition(item, 'consume')">用完</button>
          <button v-if="item.status === 'active'" :disabled="Boolean(busyId)" @tap="transition(item, 'remove')">移除</button>
        </view>
      </view>
    </view>
    <button v-if="hasMore" :disabled="loading || Boolean(busyId)" class="more-button" @tap="load()">{{ loading ? '加载中…' : '加载更多' }}</button><view class="entry-panel"><button :disabled="Boolean(busyId)" class="entry-button" @tap="openManualAdd">＋ 手动新增</button><button :disabled="Boolean(busyId)" class="entry-button" @tap="openReview">⌗ 识别录入</button></view>
  </scroll-view><BottomNav active="fridge" />
  <view v-if="editing" class="modal-mask"><view class="modal">
    <text class="panel-title">编辑库存</text>
    <input v-model="editName" :disabled="Boolean(busyId)" maxlength="120" placeholder="食材名称" />
    <view class="row-actions"><button :disabled="Boolean(busyId)" @tap="editStatus = 'known'">{{ editStatus === 'known' ? '✓ ' : '' }}已知数量</button><button :disabled="Boolean(busyId)" @tap="editStatus = 'unknown'">{{ editStatus === 'unknown' ? '✓ ' : '' }}数量未知</button></view>
    <view v-if="editStatus === 'known'"><input v-model="editQuantity" :disabled="Boolean(busyId)" type="digit" placeholder="非负数量" /><input v-model="editUnit" :disabled="Boolean(busyId)" maxlength="20" placeholder="单位" /></view>
    <input v-model="editNotes" :disabled="Boolean(busyId)" maxlength="300" placeholder="备注（可选）" />
    <view v-if="editError" class="error-copy">{{ editError }}</view>
    <button :disabled="!editCanSave || Boolean(busyId)" :loading="busyId === editing.id" @tap="saveEdit">保存</button><button :disabled="Boolean(busyId)" @tap="editing = null">取消</button>
  </view></view>
  </view>
</template>

<style scoped lang="scss">
.secondary-page { min-height: 100vh; background: #f3f4f6; }.secondary-scroll { height: calc(100vh - 100px); padding: 18px 16px; }.search-box,.filter-tabs,.list-panel,.entry-panel,.modal { background:#fff; border-radius:12px; padding:14px; }.search-box { display:flex; gap:8px; background:#e5e7eb; }.search-box input { flex:1; }.filter-tabs { display:flex; margin:14px 0; padding:0; overflow:hidden; }.filter-tabs text { flex:1; padding:12px; text-align:center; }.selected { background:#eff6ff; color:#1d4ed8; }.panel-title,.food-name,.quantity,.unknown-tag { display:block; }.panel-title { font-weight:600; padding-bottom:10px; }.inventory-row { padding:14px 0; border-top:1px solid #e5e7eb; }.food-name { font-size:16px; }.quantity { margin-top:4px; }.unknown-tag { width:max-content; margin-top:5px; padding:3px 6px; background:#fef3c7; color:#92400e; font-size:12px; }.row-actions { display:flex; gap:6px; margin-top:10px; }.row-actions button,.entry-button,.more-button { margin:0; font-size:13px; }.entry-panel { display:flex; gap:8px; margin-top:16px; }.entry-button,.more-button { flex:1; background:#fff; }.more-button { width:100%; margin-top:14px; }.error-copy { color:#b91c1c; padding:8px 0; }.state-copy { padding:30px 0; color:#71717a; }.modal-mask { position:fixed; inset:0; display:flex; align-items:center; justify-content:center; background:#0006; }.modal { width:75%; }.modal input { margin:10px 0; padding:10px; background:#f3f4f6; }.modal button { margin-top:8px; }
</style>
