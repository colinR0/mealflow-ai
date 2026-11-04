<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'
import { session } from '../../shared/session'
import { useHouseholdPage } from '../../shared/useHouseholdPage'
import type { InventoryLot } from '../../shared/types'

export default defineComponent({
  name: 'FridgeInventoryPage',
  components: { SubpageHeader, BottomNav },
  data() { return { session, search: '', filter: 'all', ...useHouseholdPage<InventoryLot>((id) => `/households/${id}/inventory-lots`) } },
  computed: {
    unknownCount(): number { return this.state.items.filter((item) => item.quantity_status === 'unknown').length },
    visibleItems(): InventoryLot[] {
      return this.state.items.filter((item) => (this.filter === 'all' || item.quantity_status === 'unknown') && item.name.includes(this.search.trim()))
    },
  },
  onShow() { void this.refresh() },
  watch: { 'session.householdId'() { void this.refresh() } },
  onPullDownRefresh() { void this.refresh().finally(() => uni.stopPullDownRefresh()) },
  methods: {
    openReview() { uni.navigateTo({ url: '/pages/fridge/review' }) },
    openManualAdd() { uni.navigateTo({ url: '/pages/fridge/manual-add' }) },
  },
})
</script>

<template>
  <view class="secondary-page">
    <SubpageHeader title="冰箱库存" />
    <scroll-view scroll-y class="secondary-scroll">
      <view class="search-box"><text class="search-icon">⌕</text><input v-model="search" placeholder="搜索已载入的食材记录" /></view>
      <view class="filter-tabs"><text :class="{ selected: filter === 'all' }" @tap="filter = 'all'">全部 ({{ state.items.length }})</text><text :class="{ selected: filter === 'unknown' }" @tap="filter = 'unknown'">数量未知 ({{ unknownCount }})</text></view>
      <view class="list-panel"><text class="panel-title">已确认食材预览</text><view v-if="state.loading" class="state-copy">正在读取库存…</view><view v-else-if="state.error" class="state-copy error-copy">{{ state.error }} <text class="retry" @tap="refresh">重试</text></view><view v-else-if="!visibleItems.length" class="state-copy">没有符合条件的已载入记录。</view><view v-else v-for="item in visibleItems" :key="item.id" class="inventory-row"><view class="row-main"><text class="food-name">{{ item.name }}</text><text v-if="item.quantity_status === 'unknown'" class="unknown-tag">数量未知 / 待核对</text><text v-else class="quantity">{{ item.quantity }} {{ item.unit || '' }}</text><text class="confirmed">确认于 {{ new Date(item.confirmed_at).toLocaleString() }}</text></view></view></view>
      <view class="entry-panel"><text class="entry-title">录入库存</text><text class="entry-copy">可以手动填写食材，也可以核对冰箱识别结果。</text><view class="entry-actions"><button class="entry-button primary-entry" @tap="openManualAdd"><text class="entry-icon">＋</text><text>手动新增</text></button><button class="entry-button" @tap="openReview"><text class="entry-icon">⌗</text><text>冰箱识别录入</text></button></view><text class="target-note">手动新增与识别写入目前均为目标态，确认后才会进入已确认库存。</text></view>
    </scroll-view>
    <BottomNav active="fridge" />
  </view>
</template>

<style scoped lang="scss">
.secondary-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }
.secondary-scroll { flex: 1; min-height: 0; padding: 18px 16px 24px; }
.search-box { display: flex; align-items: center; gap: 8px; height: 48px; padding: 0 14px; border-radius: 13px; background: #e5e7eb; color: #71717a; }.search-box input { flex: 1; min-width: 0; color: #27272a; font-size: 15px; }.search-icon { font-size: 25px; }
.filter-tabs { display: flex; margin: 18px 0; border: 1px solid #64748b; border-radius: 9px; overflow: hidden; background: #fff; }.filter-tabs text { flex: 1; padding: 11px 8px; border-right: 1px solid #64748b; color: #374151; text-align: center; font-size: 14px; }.filter-tabs text:last-child { border-right: 0; }.filter-tabs .selected { color: #1d4ed8; background: #eff6ff; font-weight: 600; }
.list-panel { overflow: hidden; border-radius: 14px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }.panel-title { display: block; padding: 18px 16px; border-bottom: 1px solid #e5e7eb; color: #18181b; font-size: 17px; font-weight: 600; }.inventory-row { padding: 16px; border-bottom: 1px solid #e5e7eb; }.inventory-row:last-child { border-bottom: 0; }.row-main { min-width: 0; }.food-name, .confirmed, .quantity, .unknown-tag { display: block; }.food-name { color: #18181b; font-size: 16px; }.quantity { margin-top: 4px; color: #27272a; font-size: 15px; }.unknown-tag, .target-label { width: fit-content; margin-top: 6px; padding: 3px 7px; border-radius: 5px; background: #fef3c7; color: #a16207; font-size: 12px; }.confirmed { margin-top: 6px; color: #71717a; font-size: 13px; }.state-copy { padding: 36px 16px; color: #71717a; font-size: 14px; line-height: 1.6; }.error-copy { color: #b91c1c; }.retry { margin-left: 12px; color: #2563eb; }.target-box { position: relative; margin-top: 22px; padding: 20px 16px; border: 1px dashed #d97706; border-radius: 12px; background: #fffbeb; }.target-label { position: absolute; top: -12px; left: 12px; margin: 0; }.target-action { display: block; color: #92400e; font-size: 14px; }
 .entry-panel { margin-top: 22px; padding: 18px 16px; border-radius: 14px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }.entry-title { display: block; color: #18181b; font-size: 17px; font-weight: 600; }.entry-copy { display: block; margin-top: 7px; color: #71717a; font-size: 13px; line-height: 1.5; }.entry-actions { display: flex; gap: 10px; margin-top: 14px; }.entry-button { flex: 1; min-width: 0; margin: 0; padding: 0 8px; border: 1px solid #d1d5db; border-radius: 9px; background: #fff; color: #374151; font-size: 13px; line-height: 44px; }.entry-button::after { border: 0; }.primary-entry { border-color: #2563eb; background: #2563eb; color: #fff; }.entry-icon { margin-right: 4px; font-size: 18px; }.target-note { display: block; margin-top: 14px; color: #a16207; font-size: 12px; line-height: 1.5; }
</style>
