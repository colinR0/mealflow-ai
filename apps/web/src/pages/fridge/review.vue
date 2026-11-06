<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'

export default defineComponent({ name: 'FridgeReviewPage', components: { SubpageHeader } })
</script>

<template>
  <view class="review-page">
    <SubpageHeader title="识别结果核对" />
    <view class="empty-card"><text class="empty-title">暂无待核对的识别结果</text><text>摄像头与识别服务尚未接入。收到真实识别结果后，才能选择食材并确认加入库存。</text></view>
  </view>
</template>

<style scoped lang="scss">
.review-page { background: #f3f4f6; padding-bottom: env(safe-area-inset-bottom); }
.empty-card { margin: 24rpx 32rpx; padding: 48rpx 32rpx; border-radius: 24rpx; background: #fff; color: #718096; font-size: 25rpx; line-height: 1.7; text-align: center; }
.empty-title { display: block; margin-bottom: 16rpx; color: #263449; font-size: 30rpx; font-weight: 650; }
</style>
