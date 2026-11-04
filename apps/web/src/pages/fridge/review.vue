<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'

export default defineComponent({
  name: 'FridgeReviewPage',
  components: { SubpageHeader, BottomNav },
  data() { return { candidates: [{ name: '鸡蛋', detail: '候选新增 · 8 个', selected: true }, { name: '番茄', detail: '现有记录 · 数量未知', selected: false }] } },
  methods: {
    mark(index: number) { this.candidates[index].selected = !this.candidates[index].selected },
    preview() { uni.showToast({ title: '目标态演示：未写入库存', icon: 'none' }) },
  },
})
</script>

<template>
  <view class="secondary-page">
    <SubpageHeader title="识别结果核对" />
    <view class="target-banner">目标态 / 模拟数据：摄像头与视觉识别服务尚未接入</view>
    <scroll-view scroll-y class="secondary-scroll">
      <view class="source-panel"><text>来源画面：冷藏室上层（模拟画面）</text><view class="mock-fridge"><view class="shelf shelf-one"></view><view class="shelf shelf-two"></view><text>视觉模拟画面</text></view><text class="source-time">模拟采集时间：今日 10:15</text></view>
      <view class="list-panel"><text class="panel-title">待核对食材</text><view v-for="(item, index) in candidates" :key="item.name" class="candidate"><view><text class="food-name">{{ item.name }}</text><text class="candidate-detail">{{ item.detail }}</text><text v-if="!index" class="unknown-tag">数量为模拟信息</text></view><view class="candidate-actions"><button :class="item.selected ? 'selected-button' : 'outline-button'" @tap="mark(index)">{{ item.selected ? (index ? '更新记录' : '确认新增') : '选择此项' }}</button><text class="ignore" @tap="mark(index)">忽略</text></view></view></view>
      <view class="write-preview">预览写入变更：将向【当前家庭】已确认库存写入 {{ candidates.filter((item) => item.selected).length }} 项变化</view>
      <button class="primary-button" @tap="preview">确认写入库存（目标态）</button>
    </scroll-view>
    <BottomNav active="fridge" />
  </view>
</template>

<style scoped lang="scss">
.secondary-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }.target-banner { padding: 12px 16px; background: #fef3c7; color: #92400e; font-size: 13px; line-height: 1.5; }.secondary-scroll { flex: 1; min-height: 0; padding: 16px; }.source-panel, .list-panel { margin-bottom: 16px; padding: 16px; border-radius: 14px; background: #fff; box-shadow: 0 4px 14px #18181b0d; color: #27272a; font-size: 14px; }.mock-fridge { position: relative; display: flex; flex-direction: column; justify-content: space-around; height: 170px; margin-top: 12px; padding: 16px 35px; border: 1px solid #d1d5db; border-radius: 9px; background: #eef0f2; color: #71717a; text-align: center; }.shelf { height: 1px; background: #9ca3af; }.source-time, .candidate-detail { display: block; margin-top: 8px; color: #71717a; font-size: 13px; }.panel-title { display: block; padding-bottom: 12px; border-bottom: 1px solid #e5e7eb; font-size: 17px; font-weight: 600; }.candidate { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 0; border-bottom: 1px solid #e5e7eb; }.candidate:last-child { border: 0; }.food-name { color: #18181b; font-size: 16px; }.unknown-tag { display: inline-block; padding: 3px 7px; border-radius: 5px; background: #fef3c7; color: #a16207; font-size: 12px; }.candidate-actions { display: flex; flex-direction: column; align-items: center; gap: 4px; }.candidate-actions button { min-width: 98px; margin: 0; padding: 0 12px; font-size: 13px; line-height: 38px; }.selected-button { background: #2563eb; color: #fff; }.outline-button { border: 1px solid #64748b; background: #fff; color: #334155; }.ignore { padding: 8px; color: #71717a; font-size: 13px; }.write-preview { padding: 12px; border: 1px solid #d1d5db; border-radius: 9px; color: #27272a; font-size: 13px; line-height: 1.5; }.primary-button { width: 100%; margin-top: 12px; background: #2563eb; color: #fff; line-height: 48px; }.primary-button::after { border: 0; }
</style>
