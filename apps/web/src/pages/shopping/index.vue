<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import { session } from '../../shared/session'
import { request } from '../../shared/transport'
import { useHouseholdPage } from '../../shared/useHouseholdPage'
import type { ShoppingItem } from '../../shared/types'

const FILTERS = [
  { id: 'pending', label: '待购买' },
  { id: 'purchased', label: '已购买' },
  { id: 'archived', label: '已归档' },
]

export default defineComponent({
  name: 'ShoppingPage',
  components: { AppHeader },
  data() {
    return {
      session, filters: FILTERS, activeStatus: 'pending', search: '',
      composer: false, name: '', quantityStatus: 'unknown' as 'known' | 'unknown',
      quantity: '', unit: '', saving: false, addError: '',
      ...useHouseholdPage<ShoppingItem>((id) => `/households/${id}/procurement-items`),
    }
  },
  computed: {
    visibleItems(): ShoppingItem[] {
      return this.state.items.filter((item) => item.status === this.activeStatus && item.name.includes(this.search.trim()))
    },
    canAdd(): boolean {
      return Boolean(this.name.trim()) && (this.quantityStatus === 'unknown' || (
        Boolean(this.quantity.trim()) && Number.isFinite(Number(this.quantity)) && Number(this.quantity) >= 0 && Boolean(this.unit.trim())
      ))
    },
  },
  onShow() { void this.refresh() },
  watch: { 'session.householdId'() { void this.refresh() }, 'session.restored'() { void this.refresh() } },
  onPullDownRefresh() { void this.refresh().finally(() => uni.stopPullDownRefresh()) },
  methods: {
    statusLabel(status: string) { return ({ pending: '待购买', purchased: '已购买', archived: '已归档' } as Record<string, string>)[status] || '待核对' },
    quantityText(item: ShoppingItem) { return item.quantity_status === 'unknown' ? '数量未知' : '购买时核对' },
    openComposer() {
      this.name = ''
      this.quantityStatus = 'unknown'
      this.quantity = ''
      this.unit = ''
      this.addError = ''
      this.composer = true
    },
    async addItem() {
      if (!session.householdId || this.saving || !this.canAdd) return
      this.saving = true
      this.addError = ''
      try {
        await request(`/households/${session.householdId}/procurement-items`, {
          name: this.name.trim(),
          quantity_status: this.quantityStatus,
          quantity: this.quantityStatus === 'known' ? this.quantity : null,
          unit: this.quantityStatus === 'known' ? this.unit.trim() : null,
        }, true, {}, 'POST')
        this.composer = false
        this.activeStatus = 'pending'
        this.search = ''
        await this.refresh()
      } catch (error) {
        this.addError = error instanceof Error ? error.message : '添加失败'
      } finally { this.saving = false }
    },
  },
})
</script>

<template>
  <view class="shopping-page">
    <AppHeader title="采购" description="查看清单。已购买不会自动入库。" />
    <view class="shopping-scroll">
      <view class="filter-tabs"><button v-for="filter in filters" :key="filter.id" :class="['filter-tab', { active: activeStatus === filter.id }]" @tap="activeStatus = filter.id">{{ filter.label }}</button></view>
      <view class="search-row"><input v-model="search" class="search-input" placeholder="搜索采购项" /></view>
      <view v-if="state.error" class="error-copy">{{ state.error }} <text @tap="refresh">重试</text></view>
      <view v-else-if="state.loading" class="empty-state">正在读取采购清单…</view>
      <view v-else-if="!visibleItems.length" class="empty-card"><text class="empty-title">暂无{{ statusLabel(activeStatus) }}项目</text><text>助手提出的采购建议会先等你确认。</text></view>
      <view v-else class="item-list"><view v-for="item in visibleItems" :key="item.id" class="item-card"><text class="item-name">{{ item.name }}</text><text class="item-qty">{{ quantityText(item) }}</text></view></view>
    </view>
    <view class="footer-actions">
      <button class="primary-button" @tap="openComposer">加入一项</button>
    </view>
    <view v-if="composer" class="modal-mask">
      <view class="modal">
        <text class="modal-title">加入采购</text>
        <input v-model="name" maxlength="120" placeholder="食材名称" />
        <view class="segmented">
          <text :class="{ selected: quantityStatus === 'unknown' }" @tap="quantityStatus = 'unknown'">数量未知</text>
          <text :class="{ selected: quantityStatus === 'known' }" @tap="quantityStatus = 'known'">已知数量</text>
        </view>
        <view v-if="quantityStatus === 'known'" class="quantity-row">
          <input v-model="quantity" type="digit" placeholder="非负数量" />
          <input v-model="unit" maxlength="20" placeholder="单位" />
        </view>
        <text v-else class="hint">数量未知不会写成 0。</text>
        <text v-if="addError" class="error-copy">{{ addError }}</text>
        <view class="modal-actions">
          <button class="primary-button" :disabled="!canAdd || saving" @tap="addItem">{{ saving ? '保存中…' : '加入待购买' }}</button>
          <button :disabled="saving" @tap="composer = false">取消</button>
        </view>
      </view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.shopping-page { min-height: 100vh; display: flex; flex-direction: column; }
.shopping-scroll { flex: 1; padding: 8px 16px 16px; }
.footer-actions { padding: 12px 16px calc(12px + env(safe-area-inset-bottom)); }
.footer-actions .primary-button { margin: 0; }
.modal-mask { position: fixed; inset: 0; z-index: 20; display: flex; align-items: flex-end; background: rgba(28, 43, 69, 0.4); }
.modal { width: 100%; padding: 20px 16px calc(20px + env(safe-area-inset-bottom)); border-radius: 20px 20px 0 0; background: var(--color-surface); }
.modal-title { display: block; margin-bottom: 12px; color: var(--color-ink); font-size: 17px; font-weight: 650; }
.modal input { width: 100%; min-height: 44px; margin: 8px 0; padding: 0 14px; border-radius: var(--radius-input); background: var(--color-paper); }
.segmented, .quantity-row, .modal-actions { display: flex; gap: 8px; margin: 8px 0; }
.segmented text { flex: 1; min-height: 40px; padding: 10px; border-radius: var(--radius-input); text-align: center; background: var(--color-paper); color: var(--color-ink-2); }
.segmented text.selected { background: var(--color-accent-soft); color: var(--color-accent-deep); font-weight: 650; }
.quantity-row input { flex: 1; }
.hint { display: block; color: var(--color-muted); font-size: 13px; }
.modal-actions button { flex: 1; margin: 0; border-radius: var(--radius-input); background: var(--color-paper); color: var(--color-ink); }
.modal-actions .primary-button { background: var(--color-accent); color: var(--color-accent-ink); }
.filter-tabs { display: flex; gap: 8px; }
.filter-tab { flex: 1; margin: 0; min-height: 40px; line-height: 40px; border-radius: var(--radius-input); background: var(--color-surface); color: var(--color-ink-2); font-size: 14px; box-shadow: var(--shadow-card); }
.filter-tab.active { background: var(--color-accent); color: var(--color-accent-ink); }
.filter-tab::after { border: 0; }
.search-row { margin-top: 12px; }
.search-input { width: 100%; height: 44px; padding: 0 14px; border-radius: var(--radius-input); background: var(--color-surface); color: var(--color-ink); font-size: 14px; box-shadow: var(--shadow-card); }
.item-list { display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }
.item-card { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 72px; padding: 16px; border-radius: var(--radius-card); background: var(--color-surface); box-shadow: var(--shadow-card); }
.item-name { flex: 1; min-width: 0; color: var(--color-ink); font-size: 16px; font-weight: 650; overflow-wrap: anywhere; }
.item-qty { color: var(--color-accent); font-size: 13px; font-variant-numeric: tabular-nums; }
.empty-card { margin-top: 16px; padding: 28px 16px; text-align: center; }
.empty-title { display: block; color: var(--color-ink); font-size: 16px; font-weight: 650; }
.empty-card text:last-child { display: block; margin-top: 8px; color: var(--color-muted); font-size: 13px; line-height: 1.6; }
.error-copy { margin-top: 14px; color: var(--color-danger); font-size: 13px; }
.error-copy text { margin-left: 10px; color: var(--color-accent); }
</style>
