<script lang="ts">
import { defineComponent } from 'vue'
import { session, selectHousehold } from '../shared/session'

export default defineComponent({
  name: 'SubpageHeader',
  props: { title: { type: String, required: true }, back: { type: Boolean, default: true } },
  data() { return { session } },
  methods: {
    goBack() { uni.navigateBack({ delta: 1, fail: () => uni.switchTab({ url: '/pages/fridge/index' }) }) },
    selectHousehold(event: { detail: { value: string } }) {
      selectHousehold(this.session.households[Number(event.detail.value)]?.id || '')
    },
  },
})
</script>

<template>
  <view class="subpage-header">
    <view class="system-spacer"></view>
    <view class="toolbar">
      <text v-if="back" class="back-button" aria-label="返回" @tap="goBack">‹</text>
      <text class="toolbar-title">{{ title }}</text>
      <picker v-if="session.households.length" class="household-picker" :range="session.households" range-key="name" :value="Math.max(0, session.households.findIndex((item) => item.id === session.householdId))" @change="selectHousehold">
        <view class="household-label">{{ session.households.find((item) => item.id === session.householdId)?.name || '选择家庭' }} <text class="chevron">⌄</text></view>
      </picker>
    </view>
  </view>
</template>

<style lang="scss">
.subpage-header { flex-shrink: 0; background: #fff; color: #18181b; border-bottom: 1px solid #e5e7eb; }
.system-spacer { height: var(--status-bar-height, 24px); background: #fff; }
.toolbar { display: flex; align-items: center; min-height: 56px; padding: 0 16px; gap: 10px; }
.back-button { display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; flex-shrink: 0; color: #27272a; font-size: 36px; line-height: 1; }
.toolbar-title { min-width: 0; flex: 1; overflow: hidden; color: #18181b; font-size: 18px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.household-picker { max-width: 42%; flex-shrink: 0; }
.household-label { display: flex; align-items: center; justify-content: flex-end; min-height: 44px; color: #52525b; font-size: 14px; white-space: nowrap; }
.chevron { padding-left: 4px; color: #71717a; }
</style>
