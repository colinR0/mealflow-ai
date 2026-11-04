<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'

export default defineComponent({
  name: 'MealPlanPage',
  components: { SubpageHeader, BottomNav },
  data() { return { dishes: [{ name: '番茄滑蛋', time: '15 分钟', basis: '依据已确认库存：鸡蛋 8 个、番茄数量未知/需核实', swapped: false }, { name: '山药瘦肉粥', time: '35 分钟', basis: '缺少食材：铁棍山药、猪瘦肉', swapped: false }] } },
  methods: {
    replace(index: number) { this.dishes[index].swapped = !this.dishes[index].swapped },
    previewShopping() { uni.showToast({ title: '目标态演示：未修改采购清单', icon: 'none' }) },
  },
})
</script>

<template>
  <view class="secondary-page">
    <SubpageHeader title="晚餐建议" />
    <view class="target-banner">目标态 / 模拟数据：晚餐方案与单菜替换预览</view>
    <scroll-view scroll-y class="secondary-scroll">
      <view class="member-panel"><text class="panel-title">用餐成员：小林、妈妈</text><text class="allergy-tag">⚠ 过敏：海鲜、花生</text><text class="restriction-tag">忌口：不吃香菜、少油少辣</text><text class="event-tag">进行中：肠胃不适（家庭自选清淡、温热）</text></view>
      <view v-for="(dish, index) in dishes" :key="dish.name" class="dish-row"><view class="dish-heading"><text class="dish-name">{{ dish.swapped ? '家常蔬菜蛋花汤' : dish.name }}</text><text class="dish-time">◷ {{ dish.time }}</text></view><text class="dish-basis">{{ dish.swapped ? '模拟替换菜品；库存与食材需重新核对。' : dish.basis }}</text><button class="outline-button" @tap="replace(index)">替换此菜　⇄</button><button v-if="index === 1" class="shopping-button" @tap="previewShopping">预览并加入采购清单（需确认）</button></view>
      <button class="primary-button" @tap="previewShopping">确认方案并开始烹饪　⌄</button>
    </scroll-view>
    <BottomNav active="assistant" />
  </view>
</template>

<style scoped lang="scss">
.secondary-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }.target-banner { padding: 12px 16px; background: #fef3c7; color: #92400e; font-size: 13px; }.secondary-scroll { flex: 1; min-height: 0; padding: 16px; }.member-panel, .dish-row { margin-bottom: 14px; padding: 16px; border-radius: 13px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }.panel-title, .dish-name { display: block; margin-bottom: 10px; color: #18181b; font-size: 17px; font-weight: 600; }.allergy-tag, .restriction-tag, .event-tag { display: block; width: fit-content; max-width: 100%; margin-top: 8px; padding: 7px 10px; border-radius: 7px; font-size: 13px; line-height: 1.45; }.allergy-tag { background: #fee2e2; color: #991b1b; }.restriction-tag { background: #e5e7eb; color: #374151; }.event-tag { background: #dcfce7; color: #166534; }.dish-heading { display: flex; justify-content: space-between; gap: 10px; }.dish-time { color: #71717a; font-size: 13px; }.dish-basis { display: block; color: #52525b; font-size: 14px; line-height: 1.6; }.outline-button, .shopping-button, .primary-button { width: 100%; margin: 12px 0 0; border-radius: 9px; font-size: 14px; line-height: 44px; }.outline-button { border: 1px solid #71717a; background: #fff; color: #27272a; }.shopping-button { background: #f8fafc; color: #1d4ed8; }.primary-button { margin: 8px 0 20px; background: #2563eb; color: #fff; }.primary-button::after, .outline-button::after, .shopping-button::after { border: 0; }
</style>
