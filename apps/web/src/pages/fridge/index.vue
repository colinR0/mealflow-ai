<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import SurfaceCard from '../../components/SurfaceCard.vue'
import { session } from '../../shared/session'
import { useHouseholdPage } from '../../shared/useHouseholdPage'
import type { InventoryLot } from '../../shared/types'
import { request } from '../../shared/transport'

export default defineComponent({
  name: 'FridgePage',
  components: { AppHeader, SurfaceCard },
  data() { return { session, search: '', totalCount: 0, unknownTotal: 0, ...useHouseholdPage<InventoryLot>((id) => `/households/${id}/inventory-lots?limit=100`) } },
  computed: {
    filtered(): InventoryLot[] { return this.state.items.filter((item) => item.name.includes(this.search.trim())) },
    unknownCount(): number { return this.unknownTotal },
  },
  onShow() { void this.refreshAll() },
  watch: { 'session.householdId'() { void this.refreshAll() } },
  onPullDownRefresh() { void this.refreshAll().finally(() => uni.stopPullDownRefresh()) },
  methods: {
    async refreshAll() {
      await this.refresh()
      const sequence = this.state.sequence
      if (!session.householdId || this.state.error) { this.totalCount = 0; this.unknownTotal = 0; return }
      try {
        const summary = await request<{ total: number; unknown: number }>(`/households/${session.householdId}/inventory-summary`)
        if (sequence !== this.state.sequence) return
        this.totalCount = summary.total
        this.unknownTotal = summary.unknown
      } catch (error) {
        if (sequence === this.state.sequence) this.state.error = error instanceof Error ? error.message : '统计加载失败'
      }
    },
    openFamily() { uni.switchTab({ url: '/pages/family/index' }) },
    openInventory() { uni.navigateTo({ url: '/pages/fridge/inventory' }) },
    openReview() { uni.navigateTo({ url: '/pages/fridge/review' }) },
    openDevice() { uni.navigateTo({ url: '/pages/fridge/device' }) },
    openRecipes() { uni.navigateTo({ url: '/pages/shopping/recipes' }) },
  },
})
</script>

<template>
  <view class="page-scroll">
    <AppHeader eyebrow="HOME" title="智慧冰箱" description="" />
    <view class="home-status"><view class="fridge-art" aria-label="冰箱产品示意图"><view class="fridge-top"><view class="fridge-display"></view><view class="fridge-handle"></view></view><view class="fridge-bottom"><view class="fridge-handle"></view></view></view><view class="status-copy"><text class="service-tag">摄像头与识别服务尚未接入</text><text class="hero-caption">示意图，非实时画面</text></view></view>
    <view class="stats-panel"><view><text class="stat-label">已确认库存</text><text class="stat-number">{{ session.householdId && !state.loading && !state.error ? totalCount : '—' }}</text></view><view><text class="stat-label">数量未知 / 待核对</text><text class="stat-number amber">{{ session.householdId && !state.loading && !state.error ? unknownCount : '—' }}</text></view></view>
    <view class="home-actions"><button class="inventory-button" @tap="openInventory"><text class="inventory-icon">▤</text><text>冰箱库存</text><text class="inventory-arrow">›</text></button></view>
    <SurfaceCard title="已确认食材预览" :action="state.loading ? '读取中' : '刷新'" @action="refreshAll">
      <view v-if="state.loading" aria-label="正在读取食材"><view v-for="n in 3" :key="n" class="skeleton"></view></view>
      <view v-else-if="state.error" class="error-state"><text>{{ state.error }}</text><text class="inline-action" @tap.stop="refreshAll">重试</text></view>
      <view v-else-if="!session.householdId" class="empty-state"><text class="empty-title">选择家庭查看共享食材</text><text>登录并选择家庭后，这里会显示已确认的库存记录。</text><button class="action-button" @tap="openFamily">前往家庭</button></view>
      <view v-else-if="!state.items.length" class="empty-state"><text class="empty-title">暂无已确认食材</text><text>摄像头与识别服务尚未接入，库存需由家庭确认后记录。</text><button class="secondary-button" @tap="openReview">识别核对（目标态）</button></view>
      <view v-else-if="!filtered.length" class="empty-state"><text>没有查到记录，请尝试其他名称。</text></view>
      <view v-else class="food-list"><view v-for="item in filtered.slice(0, 5)" :key="item.id" class="food-row"><view><text class="food-name">{{ item.name }}</text><text class="food-meta">{{ item.quantity_status === 'known' ? `${item.quantity} ${item.unit || ''}` : '数量未知 / 待核对' }}</text></view><text class="food-quantity">确认于 {{ new Date(item.confirmed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}</text></view><view class="all-inventory" @tap="openInventory">查看全部库存 ({{ state.items.length }})　›</view></view>
      <text class="footnote">展示已确认记录，搜索限已载入的最多 100 条。数量未知的食材请使用前核对。</text>
    </SurfaceCard>
    <view class="scene-grid"><button class="scene-card" @tap="openDevice"><text class="scene-title">设备与环境　›</text><text class="scene-copy">分区温湿度与告警</text></button><button class="scene-card" @tap="openRecipes"><text class="scene-title">今天吃什么　›</text><text class="scene-copy">浏览菜谱与烹饪步骤</text></button></view>
    <view class="bottom-space"></view>
  </view>
</template>

<style scoped lang="scss">
.home-status { display: flex; align-items: center; min-height: 300rpx; padding: 28rpx var(--page-gap) 46rpx; background: #f3f4f6; }
.status-copy { flex: 1; min-width: 0; padding-left: 24rpx; }
.service-tag { display: inline-block; padding: 10rpx 14rpx; border-radius: 20rpx; background: #fef3c7; color: #92400e; font-size: 21rpx; line-height: 1.4; }
.hero-caption { display: block; margin-top: 14rpx; color: #71717a; font-size: 20rpx; }
.hero-copy { flex: 1; z-index: 1; }
.hero-tag { display: block; color: #5884b6; font-size: 23rpx; }
.hero-line { display: block; }
.hero-title { display: block; margin: 18rpx 0; color: #244d7b; font-size: 43rpx; font-weight: 700; line-height: 1.45; white-space: pre-line; }
.hero-subtitle { color: #718daa; font-size: 23rpx; }
.hero-link { width: fit-content; margin: 24rpx 0 0; padding: 0 22rpx; line-height: 76rpx; border-radius: 40rpx; color: #3478f6; background: rgba(255,255,255,.8); font-size: 24rpx; }
.fridge-art { width: 190rpx; flex-shrink: 0; margin: 0 4rpx 0 12rpx; transform: perspective(600rpx) rotateY(-12deg); }
.fridge-top, .fridge-bottom { position: relative; border: 2rpx solid #fff; background: linear-gradient(105deg,#fcfdff,#d4e0ed); box-shadow: inset -12rpx 0 20rpx rgba(107,136,171,.15),12rpx 10rpx 26rpx rgba(72,113,157,.14); }
.fridge-top { height: 170rpx; border-radius: 18rpx 18rpx 3rpx 3rpx; }
.fridge-bottom { height: 160rpx; margin-top: 5rpx; border-radius: 3rpx 3rpx 16rpx 16rpx; }
.fridge-display { width: 38rpx; height: 44rpx; margin: 30rpx auto; border-radius: 6rpx; background: #6483a3; border: 6rpx solid #bbccdc; }
.fridge-handle { position: absolute; bottom: 22rpx; left: 14rpx; width: 5rpx; height: 48rpx; border-radius: 4rpx; background: #a4b6ca; }
.fridge-bottom .fridge-handle { top: 20rpx; }
.fridge-shadow { height: 16rpx; margin: 6rpx 4rpx 0; border-radius: 50%; background: rgba(72,113,157,.13); }
.stats-panel { position: relative; z-index: 1; display: flex; margin: -28rpx var(--page-gap) 22rpx; padding: 24rpx 0; border-radius: 16rpx; background: #fff; box-shadow: 0 4rpx 14rpx #18181b12; }.stats-panel > view { flex: 1; text-align: center; }.stats-panel > view + view { border-left: 1px solid #e5e7eb; }
.stats-panel .stat-number { display: block; margin-top: 8rpx; color: #1d4ed8; font-size: 46rpx; font-weight: 700; }.stats-panel .stat-number.amber { color: #d97706; }.stat-label { color: #3f3f46; font-size: 22rpx; }
.home-actions { margin: 0 var(--page-gap) 22rpx; }
.inventory-button { display: flex; align-items: center; width: 100%; height: 82rpx; margin: 0; padding: 0 24rpx; border: 1px solid #d1d5db; border-radius: 14rpx; background: #fff; color: #27272a; text-align: left; font-size: 25rpx; }.inventory-icon { margin-right: 14rpx; color: #2563eb; font-size: 30rpx; }.inventory-arrow { margin-left: auto; color: #71717a; font-size: 34rpx; }
.all-inventory { padding: 20rpx 0 4rpx; border-top: 1px solid #e5e7eb; color: #27272a; font-size: 25rpx; text-align: center; }
.scene-grid { display: flex; gap: 22rpx; margin: 0 var(--page-gap) 26rpx; }
.scene-card { flex: 1; min-width: 0; margin: 0; padding: 28rpx; text-align: left; background: rgba(255,255,255,.85); border-radius: 32rpx; line-height: 1.5; }
.scene-icon { display: flex; align-items: center; justify-content: center; width: 72rpx; height: 72rpx; border-radius: 24rpx; margin-bottom: 20rpx; font-size: 38rpx; }
.blue { background: #eaf2ff; color: #3478f6; }.orange { background: #fff2df; color: #e8a341; }
.scene-title, .scene-copy { display: block; }.scene-title { color: #2e4564; font-size: 29rpx; }.scene-copy { color: #8795aa; font-size: 23rpx; margin-top: 8rpx; font-weight: 400; }
.stats-row { display: flex; padding: 16rpx 0; }.stats-row > view { flex: 1; text-align: center; }.stats-row > view + view { border-left: 1rpx solid #edf2f9; }
.stat-number { display: block; color: #3478f6; font-size: 45rpx; font-weight: 650; }.amber { color: #d49a43; }.muted { color: #a4b4c8; }.stat-label { display: block; margin-top: 12rpx; font-size: 23rpx; color: #8391a7; }
@media (max-width: 350px) { .fridge-art { width: 150rpx; } }
</style>
