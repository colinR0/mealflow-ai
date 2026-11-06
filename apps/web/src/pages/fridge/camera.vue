<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'

export default defineComponent({ name: 'FridgeCameraPage', components: { SubpageHeader } })
</script>

<template>
  <view class="camera-page">
    <SubpageHeader title="冰箱画面" />
    <view class="camera-content">
      <view class="picture"><text class="picture-icon">▣</text><text>暂无冰箱画面</text></view>
      <view class="state-card"><text class="state-title">摄像头尚未接入</text><text class="state-copy">接入设备后，可在这里查看实时画面、截取画面并识别食材。</text></view>
      <view class="hint">识别结果需要人工核对，确认后才会加入冰箱库存。</view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.camera-page { background: #f3f4f6; }
.camera-content { padding: 24rpx 32rpx calc(32rpx + env(safe-area-inset-bottom)); }
.picture { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18rpx; aspect-ratio: 4 / 3; border-radius: 24rpx; background: #e8edf3; color: #718096; font-size: 28rpx; }
.picture-icon { color: #9caec3; font-size: 70rpx; }
.state-card { margin-top: 24rpx; padding: 30rpx; border-radius: 22rpx; background: #fff; }
.state-title, .state-copy { display: block; }.state-title { color: #263449; font-size: 30rpx; font-weight: 650; }.state-copy { margin-top: 12rpx; color: #718096; font-size: 25rpx; line-height: 1.6; }
.hint { margin-top: 24rpx; color: #8391a7; font-size: 23rpx; line-height: 1.6; }
</style>
