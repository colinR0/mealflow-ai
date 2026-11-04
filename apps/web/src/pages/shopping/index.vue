<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import SurfaceCard from '../../components/SurfaceCard.vue'
import { session } from '../../shared/session'
import { useHouseholdPage } from '../../shared/useHouseholdPage'
import type { ShoppingItem } from '../../shared/types'

const FILTERS = [
  { id: 'pending', label: '待购买' },
  { id: 'purchased', label: '已购买' },
  { id: 'archived', label: '已归档' },
]

export default defineComponent({
  name: 'ShoppingPage',
  components: { AppHeader, SurfaceCard },
  data() {
    return { session, filters: FILTERS, activeStatus: 'pending', search: '', ...useHouseholdPage<ShoppingItem>((id) => `/households/${id}/procurement-items`) }
  },
  computed: {
    visibleItems(): ShoppingItem[] {
      return this.state.items.filter((item) => item.status === this.activeStatus && item.name.includes(this.search.trim()))
    },
  },
  onShow() { void this.refresh() },
  watch: { 'session.householdId'() { void this.refresh() } },
  onPullDownRefresh() { void this.refresh().finally(() => uni.stopPullDownRefresh()) },
  methods: {
    statusLabel(status: string) { return ({ pending: '待购买', purchased: '已购买', archived: '已归档' } as Record<string, string>)[status] || '待核对' },
    openAssistant() { uni.switchTab({ url: '/pages/assistant/index' }) },
    openStockIn() { uni.navigateTo({ url: '/pages/shopping/stock-in' }) },
  },
})
</script>

<template>
  <view class="shopping-page">
    <AppHeader eyebrow="SHOPPING" title="家庭采购清单" description="林家共享采购任务" />
    <scroll-view scroll-y class="shopping-scroll">
      <view class="summary-strip"><view><text class="summary-number">{{ state.items.filter((item) => item.status === 'pending').length }}</text><text>待购买</text></view><view><text class="summary-number">{{ state.items.filter((item) => item.status === 'purchased').length }}</text><text>已购买待入库</text></view><text class="summary-note">数量未知项请在购买时核对</text></view>
      <view class="filter-tabs"><button v-for="filter in filters" :key="filter.id" :class="['filter-tab', { active: activeStatus === filter.id }]" @tap="activeStatus = filter.id">{{ filter.label }}</button></view>
      <SurfaceCard :title="statusLabel(activeStatus)" :action="state.loading ? '读取中' : '刷新'" @action="refresh">
        <view class="search-row"><input v-model="search" class="search-input" placeholder="搜索采购项" /><text class="count-label">{{ visibleItems.length }} 项</text></view>
        <view v-if="state.error" class="error-copy">{{ state.error }} <text @tap="refresh">重试</text></view>
        <view v-else-if="state.loading" class="empty-state">正在读取共享采购清单…</view>
        <view v-else-if="!visibleItems.length" class="empty-state"><text class="empty-title">暂无{{ statusLabel(activeStatus) }}项目</text><text>助手提出的采购建议会先等待你的确认。</text><button class="secondary-button" @tap="openAssistant">去问问助手</button></view>
        <view v-else class="food-list"><view v-for="item in visibleItems" :key="item.id" class="food-row"><view><text class="food-name">{{ item.name }}</text><text class="food-meta">{{ item.quantity_status === 'unknown' ? '数量未知 / 待核对' : '数量以购买时实际包装为准' }}</text></view><text class="food-quantity">{{ statusLabel(item.status) }}</text></view></view>
      </SurfaceCard>
      <view v-if="activeStatus === 'purchased'" class="target-action" @tap="openStockIn"><text class="target-chip">目标态 / 待接口支持</text><text>购买记录核对并确认入库　›</text></view>
      <text class="footnote">已购买不等于已入库，入库前需独立核对实际数量。当前采购状态修改与入库写入尚未接入。</text>
    </scroll-view>
  </view>
</template>

<style scoped lang="scss">
.shopping-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }
.shopping-scroll { flex: 1; min-height: 0; padding: 18px 0 24px; }
.summary-strip { display: flex; align-items: center; gap: 18px; margin: 0 16px 16px; padding: 15px 16px; border-radius: 13px; background: #18181b; color: #d4d4d8; font-size: 12px; }.summary-strip > view { display: flex; flex-direction: column; gap: 3px; }.summary-number { color: #fff; font-size: 20px; font-weight: 700; }.summary-note { margin-left: auto; max-width: 110px; color: #fbbf24; line-height: 1.4; text-align: right; }
.filter-tabs { display: flex; gap: 0; padding: 0 16px; border-bottom: 1px solid #e5e7eb; background: #fff; }.filter-tab { flex: 1; margin: 0; padding: 0 8px; border-radius: 0; border-bottom: 3px solid transparent; color: #71717a; font-size: 14px; line-height: 46px; background: #fff; }.filter-tab.active { border-bottom-color: #2563eb; color: #1d4ed8; }.filter-tab::after { border: 0; }
.search-row { display: flex; align-items: center; gap: 10px; }.search-input { flex: 1; min-width: 0; height: 42px; padding: 0 12px; border-radius: 9px; background: #f3f4f6; color: #27272a; font-size: 14px; }.count-label { color: #71717a; font-size: 13px; }.food-meta { display: block; margin-top: 5px; color: #71717a; font-size: 13px; }.food-quantity { color: #52525b; font-size: 13px; }.error-copy { margin-top: 14px; color: #b91c1c; font-size: 13px; }.error-copy text { margin-left: 10px; color: #1d4ed8; }.target-action { position: relative; display: flex; justify-content: space-between; margin: 0 16px 16px; padding: 28px 16px 16px; border: 1px dashed #d97706; border-radius: 12px; background: #fffbeb; color: #92400e; font-size: 14px; }.target-chip { position: absolute; top: -11px; left: 12px; padding: 3px 8px; border-radius: 6px; background: #fef3c7; color: #a16207; font-size: 12px; }.footnote { display: block; margin: 0 16px; color: #71717a; font-size: 12px; line-height: 1.6; }
</style>
