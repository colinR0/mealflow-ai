<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'
import { session } from '../../shared/session'
import { ApiError, request } from '../../shared/transport'
import type { InventoryLot, InventoryPage } from '../../shared/types'

export default defineComponent({
  name: 'FridgeInventoryPage',
  components: { SubpageHeader, BottomNav },
  data() {
    return {
      session,
      items: [] as InventoryLot[],
      total: 0,
      offset: 0,
      limit: 30,
      hasMore: false,
      search: '',
      filter: 'all' as 'all' | 'known' | 'unknown',
      status: 'active' as 'active' | 'consumed' | 'removed' | 'all',
      loading: false,
      busyId: '',
      error: '',
      errorRetry: 'page' as 'page' | 'refresh',
      searchTimer: undefined as ReturnType<typeof setTimeout> | undefined,
      generation: 0,
      detail: null as InventoryLot | null,
      editing: null as InventoryLot | null,
      editName: '',
      editStatus: 'unknown' as 'known' | 'unknown',
      editQuantity: '',
      editUnit: '',
      editNotes: '',
      editError: '',
      pendingConflict: null as null | { action: 'save' | 'consume' | 'remove'; item: InventoryLot },
    }
  },
  computed: {
    householdLabel(): string {
      return this.session.households.find((item) => item.id === this.session.householdId)?.name || '当前家庭'
    },
    editCanSave(): boolean {
      return Boolean(this.editName.trim()) && (this.editStatus === 'unknown' || (
        Boolean(this.editQuantity.trim())
        && Number.isFinite(Number(this.editQuantity))
        && Number(this.editQuantity) >= 0
        && Boolean(this.editUnit.trim())
      ))
    },
  },
  onShow() { void this.refresh() },
  onUnload() { clearTimeout(this.searchTimer); this.generation++ },
  onPullDownRefresh() { void this.refresh().finally(() => uni.stopPullDownRefresh()) },
  watch: {
    'session.householdId'() { this.closePanels(); void this.refresh() },
    filter() { void this.refresh() },
    status() { void this.refresh() },
    search() {
      clearTimeout(this.searchTimer)
      this.reset()
      this.searchTimer = setTimeout(() => void this.load(), 250)
    },
  },
  methods: {
    closePanels() {
      this.detail = null
      this.editing = null
      this.pendingConflict = null
      this.editError = ''
    },
    reset() {
      this.generation++
      this.loading = false
      this.offset = 0
      this.items = []
      this.total = 0
      this.hasMore = false
      this.error = ''
    },
    async refresh() {
      clearTimeout(this.searchTimer)
      this.reset()
      await this.load()
    },
    quantityQuery(): string {
      if (this.filter === 'known') return '&quantity_status=known'
      if (this.filter === 'unknown') return '&quantity_status=unknown'
      return ''
    },
    async load() {
      if (!session.householdId || this.loading) return
      const generation = this.generation
      this.loading = true
      this.error = ''
      try {
        const query = `/households/${session.householdId}/inventory-lots?offset=${this.offset}&limit=${this.limit}&status=${this.status}${this.quantityQuery()}&search=${encodeURIComponent(this.search.trim())}`
        const response = await request<InventoryPage | InventoryLot[]>(query)
        if (generation !== this.generation) return
        if (Array.isArray(response)) {
          this.items = this.items.concat(response)
          this.total = response.length
          this.offset = response.length
          this.hasMore = false
        } else if (Array.isArray(response.items)) {
          this.items = this.items.concat(response.items)
          this.total = response.total
          this.offset = response.offset + response.items.length
          this.hasMore = response.has_more
        } else {
          throw new Error('库存接口响应格式无效，请确认后端服务已更新')
        }
      } catch (error) {
        if (generation === this.generation) {
          this.error = error instanceof Error ? error.message : '加载失败'
          this.errorRetry = 'page'
        }
      } finally {
        if (generation === this.generation) this.loading = false
      }
    },
    openManualAdd() { uni.navigateTo({ url: '/pages/fridge/manual-add' }) },
    openDetail(item: InventoryLot) { if (!this.busyId) this.detail = item },
    quantityText(item: InventoryLot): string {
      return item.quantity_status === 'known' ? `${item.quantity ?? ''} ${item.unit ?? ''}`.trim() : '数量未知'
    },
    statusText(item: InventoryLot): string {
      if (item.status === 'consumed') return '已用完'
      if (item.status === 'removed') return '已移除'
      return '在库'
    },
    edit(item: InventoryLot) {
      if (this.busyId) return
      this.detail = null
      this.editing = item
      this.editName = item.name
      this.editStatus = item.quantity_status
      this.editQuantity = item.quantity ?? ''
      this.editUnit = item.unit ?? ''
      this.editNotes = item.notes
      this.editError = ''
    },
    async confirmConflict(): Promise<boolean> {
      return await new Promise((resolve) => {
        uni.showModal({
          title: '记录已变化',
          content: '已刷新为最新内容，请确认后重新提交。',
          confirmText: '重新确认',
          success: (res) => resolve(Boolean(res.confirm)),
        })
      })
    },
    async handleConflict(action: 'save' | 'consume' | 'remove', item: InventoryLot) {
      await this.refresh()
      const latest = this.items.find((row) => row.id === item.id)
      if (!latest) {
        this.error = '记录已不存在，请刷新后重试'
        this.errorRetry = 'refresh'
        this.closePanels()
        return
      }
      const confirmed = await this.confirmConflict()
      if (!confirmed) return
      if (action === 'save') {
        this.edit(latest)
        return
      }
      await this.transition(latest, action, true)
    },
    async saveEdit() {
      if (!this.editing || this.busyId) return
      if (!this.editName.trim()) { this.editError = '请填写食材名称'; return }
      if (!this.editCanSave) { this.editError = '请填写非负数量和单位'; return }
      const item = this.editing
      const householdId = session.householdId
      const payload = {
        expected_version: item.version,
        name: this.editName.trim(),
        quantity_status: this.editStatus,
        quantity: this.editStatus === 'known' ? this.editQuantity : null,
        unit: this.editStatus === 'known' ? this.editUnit.trim() : null,
        notes: this.editNotes.trim(),
      }
      this.busyId = item.id
      this.editError = ''
      try {
        await request(`/households/${householdId}/inventory-lots/${item.id}`, payload, true, {}, 'PATCH')
        this.editing = null
        await this.refresh()
      } catch (error) {
        if (error instanceof ApiError && error.statusCode === 409) {
          this.editing = null
          await this.handleConflict('save', item)
          return
        }
        this.editError = error instanceof Error ? error.message : '保存失败'
        this.error = this.editError
        this.errorRetry = 'refresh'
        if (householdId !== session.householdId) this.editing = null
      } finally { this.busyId = '' }
    },
    async transition(item: InventoryLot, action: 'consume' | 'remove', skipConfirm = false) {
      if (this.busyId) return
      const householdId = session.householdId
      this.busyId = item.id
      this.error = ''
      try {
        if (!skipConfirm) {
          const confirmed = await new Promise<boolean>((resolve, reject) => uni.showModal({
            title: action === 'consume' ? '标记用完' : '移除库存',
            content: action === 'consume' ? `确认将“${item.name}”标记为用完？` : `确认移除“${item.name}”？`,
            success: (res) => resolve(Boolean(res.confirm)),
            fail: reject,
          }))
          if (!confirmed || householdId !== session.householdId) return
        }
        await request(
          `/households/${householdId}/inventory-lots/${item.id}${action === 'consume' ? '/consume' : ''}`,
          { expected_version: item.version },
          true,
          {},
          action === 'consume' ? 'POST' : 'DELETE',
        )
        this.detail = null
        await this.refresh()
      } catch (error) {
        if (error instanceof ApiError && error.statusCode === 409) {
          await this.handleConflict(action, item)
          return
        }
        this.error = error instanceof Error ? error.message : '操作失败，请重试'
        this.errorRetry = 'refresh'
      } finally { this.busyId = '' }
    },
    retryError() {
      if (this.errorRetry === 'refresh') {
        this.closePanels()
        return this.refresh()
      }
      return this.load()
    },
  },
})
</script>

<template>
  <view class="inventory-page">
    <SubpageHeader title="冰箱库存" :switchable="false" />
    <view class="inventory-body">
      <view class="toolbar-card">
        <view class="household-chip"><text class="chip-label">家庭</text><text class="chip-value">{{ householdLabel }}</text></view>
        <view class="search-box"><input v-model="search" confirm-type="search" placeholder="搜索食材名称" /></view>
        <view class="filter-row">
          <text :class="{ selected: status === 'active' }" @tap="status = 'active'">在库</text>
          <text :class="{ selected: status === 'consumed' }" @tap="status = 'consumed'">已用完</text>
          <text :class="{ selected: status === 'removed' }" @tap="status = 'removed'">已移除</text>
          <text :class="{ selected: status === 'all' }" @tap="status = 'all'">全部</text>
        </view>
        <view class="filter-row quantity-row">
          <text :class="{ selected: filter === 'all' }" @tap="filter = 'all'">全部数量</text>
          <text :class="{ selected: filter === 'known' }" @tap="filter = 'known'">已知数量</text>
          <text :class="{ selected: filter === 'unknown' }" @tap="filter = 'unknown'">数量未知</text>
        </view>
      </view>

      <view v-if="error" class="error-banner">{{ error }}<text class="retry" @tap="retryError">重试</text></view>

      <view class="list-meta">匹配 {{ total }} 条</view>

      <view v-if="loading && !items.length" class="state-copy">正在读取库存…</view>
      <view v-else-if="!items.length" class="empty-card">
        <text class="empty-title">暂无符合条件的库存</text>
        <text class="empty-copy">可手动登记常用食材；识别与相机入口尚未接入。</text>
        <button class="primary-action" :disabled="Boolean(busyId)" @tap="openManualAdd">手动新增</button>
      </view>

      <view v-for="item in items" :key="item.id" class="lot-card" @tap="openDetail(item)">
        <view class="lot-main">
          <text class="lot-name">{{ item.name }}</text>
          <text class="lot-quantity">{{ quantityText(item) }}</text>
        </view>
        <view class="lot-side">
          <text class="status-pill" :class="item.status">{{ statusText(item) }}</text>
          <text class="lot-notes" v-if="item.notes">{{ item.notes }}</text>
        </view>
        <view class="lot-actions" @tap.stop>
          <button size="mini" :disabled="Boolean(busyId)" @tap="edit(item)">编辑</button>
          <button v-if="item.status === 'active'" size="mini" :disabled="Boolean(busyId)" @tap="transition(item, 'consume')">用完</button>
          <button v-if="item.status === 'active'" size="mini" :disabled="Boolean(busyId)" @tap="transition(item, 'remove')">移除</button>
        </view>
      </view>

      <button v-if="hasMore" class="more-button" :disabled="loading || Boolean(busyId)" @tap="load">{{ loading ? '加载中…' : '加载更多' }}</button>
    </view>

    <view class="footer-actions">
      <button class="primary-action" :disabled="Boolean(busyId)" @tap="openManualAdd">＋ 手动新增</button>
    </view>
    <BottomNav active="fridge" />

    <view v-if="detail" class="modal-mask" @tap="detail = null">
      <view class="modal" @tap.stop>
        <text class="modal-title">库存详情</text>
        <view class="detail-row"><text>名称</text><text>{{ detail.name }}</text></view>
        <view class="detail-row"><text>数量</text><text>{{ quantityText(detail) }}</text></view>
        <view class="detail-row"><text>状态</text><text>{{ statusText(detail) }}</text></view>
        <view class="detail-row"><text>备注</text><text>{{ detail.notes || '无' }}</text></view>
        <view class="detail-row"><text>确认时间</text><text>{{ new Date(detail.confirmed_at).toLocaleString() }}</text></view>
        <view class="modal-actions">
          <button :disabled="Boolean(busyId)" @tap="edit(detail)">编辑</button>
          <button v-if="detail.status === 'active'" :disabled="Boolean(busyId)" @tap="transition(detail, 'consume')">用完</button>
          <button v-if="detail.status === 'active'" :disabled="Boolean(busyId)" @tap="transition(detail, 'remove')">移除</button>
          <button :disabled="Boolean(busyId)" @tap="detail = null">关闭</button>
        </view>
      </view>
    </view>

    <view v-if="editing" class="modal-mask">
      <view class="modal">
        <text class="modal-title">编辑库存</text>
        <input v-model="editName" :disabled="Boolean(busyId)" maxlength="120" placeholder="食材名称" />
        <view class="segmented">
          <text :class="{ selected: editStatus === 'known' }" @tap="editStatus = 'known'">已知数量</text>
          <text :class="{ selected: editStatus === 'unknown' }" @tap="editStatus = 'unknown'">数量未知</text>
        </view>
        <view v-if="editStatus === 'known'" class="edit-quantity">
          <input v-model="editQuantity" :disabled="Boolean(busyId)" type="digit" placeholder="非负数量" />
          <input v-model="editUnit" :disabled="Boolean(busyId)" maxlength="20" placeholder="单位" />
        </view>
        <input v-model="editNotes" :disabled="Boolean(busyId)" maxlength="300" placeholder="备注（可选）" />
        <view v-if="editError" class="error-banner">{{ editError }}</view>
        <view class="modal-actions">
          <button :disabled="!editCanSave || Boolean(busyId)" :loading="busyId === editing.id" @tap="saveEdit">保存</button>
          <button :disabled="Boolean(busyId)" @tap="editing = null">取消</button>
        </view>
      </view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.inventory-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }
.inventory-body { flex: 1; min-height: 0; padding: 12px 16px 8px; padding-bottom: 8px; overflow: auto; }
.toolbar-card, .lot-card, .empty-card, .modal { background: #fff; border-radius: 14px; box-shadow: 0 4px 14px #18181b0d; }
.toolbar-card { padding: 12px; }
.household-chip { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; color: #52525b; font-size: 13px; }
.chip-label { padding: 2px 8px; border-radius: 999px; background: #eff6ff; color: #1d4ed8; }
.chip-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.search-box { padding: 10px 12px; border-radius: 10px; background: #f3f4f6; }
.search-box input { width: 100%; font-size: 14px; }
.filter-row { display: flex; gap: 6px; margin-top: 10px; }
.filter-row text { flex: 1; min-width: 0; padding: 8px 4px; border-radius: 8px; text-align: center; color: #52525b; font-size: 12px; background: #f8fafc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.filter-row text.selected { background: #eff6ff; color: #1d4ed8; font-weight: 600; }
.quantity-row text { flex: 1; }
.list-meta { margin: 12px 2px 8px; color: #71717a; font-size: 12px; }
.lot-card { margin-bottom: 10px; padding: 14px; }
.lot-main { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.lot-name, .lot-quantity, .lot-notes { overflow-wrap: anywhere; }
.lot-name { flex: 1; min-width: 0; color: #18181b; font-size: 16px; font-weight: 650; }
.lot-quantity { max-width: 42%; color: #1d4ed8; font-size: 14px; text-align: right; }
.lot-side { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.status-pill { padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #ecfdf5; color: #047857; }
.status-pill.consumed { background: #fff7ed; color: #c2410c; }
.status-pill.removed { background: #f4f4f5; color: #52525b; }
.lot-notes { flex: 1; min-width: 0; color: #71717a; font-size: 12px; }
.lot-actions { display: flex; gap: 8px; margin-top: 12px; }
.lot-actions button, .modal-actions button, .more-button { margin: 0; }
.empty-card { padding: 28px 16px; text-align: center; }
.empty-title { display: block; color: #18181b; font-size: 16px; font-weight: 600; }
.empty-copy { display: block; margin: 8px 0 16px; color: #71717a; font-size: 13px; line-height: 1.6; }
.state-copy { padding: 28px 0; color: #71717a; text-align: center; }
.more-button { width: 100%; margin: 4px 0 12px; background: #fff; }
.footer-actions { padding: 10px 16px 12px; background: #f3f4f6; border-top: 1px solid #e5e7eb; }
.primary-action { margin: 0; border-radius: 10px; background: #2563eb; color: #fff; line-height: 44px; font-size: 14px; }
.error-banner { margin: 10px 0; padding: 10px 12px; border-radius: 10px; background: #fef2f2; color: #b91c1c; font-size: 13px; }
.retry { margin-left: 10px; color: #2563eb; }
.modal-mask { position: fixed; inset: 0; z-index: 20; display: flex; align-items: flex-end; justify-content: center; background: #0006; }
.modal { width: 100%; max-height: 80vh; padding: 18px 16px calc(18px + env(safe-area-inset-bottom)); border-radius: 18px 18px 0 0; overflow: auto; }
.modal-title { display: block; margin-bottom: 12px; font-size: 17px; font-weight: 650; }
.modal input { width: 100%; margin: 8px 0; padding: 10px 12px; border-radius: 10px; background: #f3f4f6; }
.segmented, .edit-quantity, .modal-actions { display: flex; gap: 8px; margin: 8px 0; }
.segmented text { flex: 1; padding: 10px; border-radius: 8px; text-align: center; background: #f3f4f6; color: #52525b; }
.segmented text.selected { background: #eff6ff; color: #1d4ed8; }
.edit-quantity input { flex: 1; }
.detail-row { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f4f4f5; color: #52525b; font-size: 13px; }
.detail-row text:last-child { max-width: 65%; color: #18181b; text-align: right; overflow-wrap: anywhere; }
.modal-actions button { flex: 1; }
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
</style>
