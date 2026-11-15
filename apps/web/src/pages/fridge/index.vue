<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'

export default defineComponent({
  name: 'FridgePage',
  components: { AppHeader },
  methods: {
    openInventory() { uni.navigateTo({ url: '/pages/fridge/inventory' }) },
    openDevice() { uni.navigateTo({ url: '/pages/fridge/device' }) },
    openCamera() { uni.navigateTo({ url: '/pages/fridge/camera' }) },
    openRecipes() { uni.navigateTo({ url: '/pages/shopping/recipes' }) },
  },
})
</script>

<template>
  <view class="page-scroll home-page">
    <AppHeader title="智慧冰箱" />
    <view class="camera-entry" @tap="openCamera">
      <view>
        <text class="camera-title">冰箱画面　›</text>
        <text class="camera-status">摄像头尚未接入，暂无实时画面</text>
      </view>
      <text class="camera-icon">▣</text>
    </view>
    <view class="tile-grid">
      <view class="tile" @tap="openInventory"><text class="tile-mark">库</text><text class="tile-title">冰箱库存</text><text class="tile-copy">查看已确认食材</text></view>
      <view class="tile" @tap="openDevice"><text class="tile-mark">备</text><text class="tile-title">设备与环境</text><text class="tile-copy">分区温湿度与告警</text></view>
      <view class="tile" @tap="openRecipes"><text class="tile-mark">菜</text><text class="tile-title">今天吃什么</text><text class="tile-copy">浏览菜谱和做法</text></view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.home-page { min-height: 100vh; padding-bottom: 96px; }
.camera-entry { display: flex; align-items: center; justify-content: space-between; margin: 8rpx var(--page-gap) 20rpx; padding: 36rpx 32rpx; border-radius: 28rpx; background: linear-gradient(135deg, #ffffff, #e7f2ff); color: var(--color-ink); box-shadow: var(--shadow-card); }
.camera-title, .camera-status { display: block; }
.camera-title { font-size: 32rpx; font-weight: 700; }
.camera-status { margin-top: 10rpx; color: var(--color-muted); font-size: 23rpx; }
.camera-icon { font-size: 48rpx; color: var(--color-accent); }
.tile-grid { display: flex; flex-wrap: wrap; gap: 12px; margin: 0 var(--page-gap) 16px; }
.tile { width: calc(50% - 6px); min-height: 132px; padding: 16px 14px; border-radius: 20px; background: var(--color-surface); box-shadow: var(--shadow-card); }
.tile-mark { display: flex; align-items: center; justify-content: center; width: 56rpx; height: 56rpx; margin-bottom: 18rpx; border-radius: 16rpx; background: var(--color-accent-soft); color: var(--color-accent); font-size: 24rpx; font-weight: 700; }
.tile-title, .tile-copy { display: block; }
.tile-title { color: var(--color-ink); font-size: 28rpx; font-weight: 700; }
.tile-copy { margin-top: 8rpx; color: var(--color-muted); font-size: 22rpx; line-height: 1.5; }
</style>
