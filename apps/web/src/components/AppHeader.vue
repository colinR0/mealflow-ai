<script lang="ts">
import { defineComponent } from 'vue'
import { session, selectHousehold } from '../shared/session'

export default defineComponent({
  name: 'AppHeader',
  props: { eyebrow: { type: String, required: true }, title: { type: String, required: true }, description: { type: String, default: '' }, tone: { type: String, default: 'light' } },
  data() { return { session } },
  methods: { selectHousehold(event: { detail: { value: string } }) { selectHousehold(this.session.households[Number(event.detail.value)]?.id || '') } },
})
</script>

<template>
  <view class="app-header" :class="`tone-${tone}`">
    <template v-if="tone === 'dark'">
      <view class="brand-row"><view class="brand-name">智慧冰箱</view>
        <picker v-if="session.households.length" class="picker-wrap" :range="session.households" range-key="name" :value="Math.max(0, session.households.findIndex((item) => item.id === session.householdId))" @change="selectHousehold"><view class="household-picker">{{ session.households.find((item) => item.id === session.householdId)?.name || '选择家庭' }} ⌄</view></picker>
      </view>
    </template>
    <view v-else class="light-toolbar"><text class="heading-title">{{ title }}</text><picker v-if="session.households.length" class="picker-wrap" :range="session.households" range-key="name" :value="Math.max(0, session.households.findIndex((item) => item.id === session.householdId))" @change="selectHousehold"><view class="household-picker">{{ session.households.find((item) => item.id === session.householdId)?.name || '选择家庭' }}⌄</view></picker></view>
    <text v-if="description && tone !== 'dark'" class="heading-description">{{ description }}</text>
  </view>
</template>

<style lang="scss">
.app-header { padding: calc(var(--status-bar-height, 0px) + 8rpx) var(--page-gap) 14rpx; flex-shrink: 0; background: #fff; border-bottom: 1px solid #e5e7eb; }
.light-toolbar { display: flex; align-items: center; min-height: 56px; gap: 10px; }
.brand-row { display: flex; align-items: center; gap: 14rpx; min-height: 72rpx; }
.brand-name { color: #18181b; font-size: 34rpx; font-weight: 750; line-height: 1.25; }
.brand-name text { display: block; margin-top: 8rpx; color: #7189aa; font-size: 22rpx; font-weight: 400; letter-spacing: 2rpx; }
.picker-wrap { margin-left: auto; min-width: 0; max-width: 45%; }
.household-picker { padding: 22rpx; min-height: 88rpx; line-height: 44rpx; border: 1rpx solid #e5e7eb; border-radius: 16rpx; background: #fff; color: #374151; font-size: 24rpx; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.page-heading { padding: 36rpx 0 8rpx; }
.eyebrow { display: none; }
.heading-title { display: block; min-width: 0; flex: 1; overflow: hidden; color: #18181b; font-size: 36rpx; font-weight: 700; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.heading-description { display: block; margin: 2rpx 0 8rpx; color: #71717a; font-size: 22rpx; line-height: 1.5; }
.app-header.tone-dark { padding-top: calc(var(--status-bar-height, 0px) + 20rpx); padding-bottom: 20rpx; background: #18181b; border-bottom-color: #27272a; }
.tone-dark .brand-name, .tone-dark .heading-title { color: #fff; }
.tone-dark .brand-name text, .tone-dark .heading-description { color: #a1a1aa; }
.tone-dark .household-picker { border-color: #52525b; background: #27272a; color: #e4e4e7; }
.tone-dark .heading-title { flex: initial; color: #fff; }
</style>
