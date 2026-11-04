<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'

export default defineComponent({
  name: 'StockInPage',
  components: { SubpageHeader, BottomNav },
  data() { return { items: [{ name: '鲜牛奶', purchased: '购买数量：2 盒', quantity: '2', unit: '盒', unknown: false }, { name: '生姜', purchased: '购买数量：待确认', quantity: '', unit: '克', unknown: true }] } },
  computed: { selectedCount(): number { return this.items.length } },
  methods: {
    confirm() { uni.showToast({ title: '目标态演示：没有写入库存', icon: 'none' }) },
    toggleUnknown(index: number, event: Event) {
      const value = (event as CustomEvent<{ value: boolean }>).detail.value
      this.items[index].unknown = value
      if (value) this.items[index].quantity = ''
    },
    cancel() { uni.navigateBack({ delta: 1 }) },
  },
})
</script>

<template>
  <view class="secondary-page">
    <SubpageHeader title="确认入库" />
    <view class="target-banner">目标态 / 模拟数据：已购买不等于已入库，需独立核对写入</view>
    <scroll-view scroll-y class="secondary-scroll"><text class="intro">从【已购买】清单发起核对，确认后才会写入林家冰箱库存：</text>
      <view class="items-panel"><view v-for="(item, index) in items" :key="item.name" class="stock-row"><text class="item-name">{{ item.name }}</text><text class="purchased">{{ item.purchased }}</text><view class="quantity-row"><text>实际数量</text><view class="quantity-control"><input v-model="item.quantity" type="number" :disabled="item.unknown" placeholder="输入数量" /><text>{{ item.unit }}</text></view></view><view class="unknown-row"><text>数量未知 / 待核对</text><switch :checked="item.unknown" color="#d97706" @change="toggleUnknown(index, $event)" /></view><text v-if="index === 0" class="unit-note">请按实际包装核对数量</text></view></view>
    </scroll-view>
    <view class="submit-area"><text class="write-preview">写入预览：将向【当前家庭】已确认库存新增 {{ selectedCount }} 条记录</text><view class="button-row"><button class="cancel-button" @tap="cancel">取消</button><button class="confirm-button" @tap="confirm">确认入库（目标态）</button></view></view>
    <BottomNav active="shopping" />
  </view>
</template>

<style scoped lang="scss">
.secondary-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }.target-banner { padding: 12px 16px; background: #fef3c7; color: #92400e; font-size: 13px; line-height: 1.5; }.secondary-scroll { flex: 1; min-height: 0; padding: 18px 16px; }.intro { display: block; margin-bottom: 14px; color: #27272a; font-size: 15px; line-height: 1.6; }.items-panel { overflow: hidden; border-radius: 14px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }.stock-row { padding: 16px; border-bottom: 1px solid #e5e7eb; }.stock-row:last-child { border: 0; }.item-name, .purchased { display: block; }.item-name { color: #18181b; font-size: 17px; font-weight: 600; }.purchased { margin-top: 4px; color: #71717a; font-size: 13px; }.quantity-row, .unknown-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 16px; margin-top: 12px; border-top: 1px solid #e5e7eb; color: #3f3f46; font-size: 14px; }.quantity-control { display: flex; overflow: hidden; align-items: center; border-radius: 8px; background: #fef3c7; }.quantity-control input { width: 72px; padding: 8px; text-align: right; color: #1d4ed8; }.quantity-control text { padding: 8px; background: #f3f4f6; }.unit-note { display: block; margin-top: 8px; color: #71717a; font-size: 12px; }.submit-area { padding: 12px 16px 16px; border-top: 1px solid #e5e7eb; background: #fff; }.write-preview { display: block; padding: 12px; border: 1px solid #d1d5db; border-radius: 9px; color: #3f3f46; font-size: 13px; line-height: 1.5; }.button-row { display: flex; gap: 10px; margin-top: 12px; }.cancel-button, .confirm-button { flex: 1; margin: 0; border-radius: 9px; font-size: 14px; line-height: 44px; }.cancel-button { background: #e5e7eb; color: #27272a; }.confirm-button { background: #2563eb; color: #fff; }.cancel-button::after, .confirm-button::after { border: 0; }
</style>
