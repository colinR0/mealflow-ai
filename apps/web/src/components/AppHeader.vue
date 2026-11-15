<script lang="ts">
import { defineComponent } from 'vue'

export default defineComponent({
  name: 'AppHeader',
  props: { eyebrow: { type: String, default: '' }, title: { type: String, required: true }, description: { type: String, default: '' }, tone: { type: String, default: 'light' } },
})
</script>

<template>
  <view class="app-header" :class="`tone-${tone}`">
    <template v-if="tone === 'dark'">
      <view class="brand-row"><view class="brand-name">智慧冰箱</view></view>
    </template>
    <view v-else class="light-toolbar"><text class="heading-title">{{ title }}</text></view>
    <text v-if="description && tone !== 'dark'" class="heading-description">{{ description }}</text>
  </view>
</template>

<style lang="scss">
.app-header { padding: calc(var(--status-bar-height, 0px) + 8rpx) var(--page-gap) 8rpx; flex-shrink: 0; background: transparent; }
.light-toolbar { display: flex; align-items: center; min-height: 56px; gap: 10px; }
.brand-row { display: flex; align-items: center; gap: 14rpx; min-height: 72rpx; }
.brand-name { color: var(--color-ink); font-size: 34rpx; font-weight: 750; line-height: 1.25; }
.brand-name text { display: block; margin-top: 8rpx; color: var(--color-muted); font-size: 22rpx; font-weight: 400; }
.picker-wrap { margin-left: auto; min-width: 0; max-width: 45%; }
.household-picker { padding: 22rpx; min-height: 88rpx; line-height: 44rpx; border: 1rpx solid var(--color-rule); border-radius: var(--radius-input); background: var(--color-surface); color: var(--color-ink-2); font-size: 24rpx; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.page-heading { padding: 36rpx 0 8rpx; }
.eyebrow { display: none; }
.heading-title { display: block; min-width: 0; flex: 1; overflow: hidden; color: var(--color-ink); font-size: 36rpx; font-weight: 700; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.heading-description { display: block; margin: 2rpx 0 8rpx; color: var(--color-muted); font-size: 22rpx; line-height: 1.5; }
.app-header.tone-dark { padding-top: calc(var(--status-bar-height, 0px) + 20rpx); padding-bottom: 20rpx; background: var(--color-ink); border-bottom-color: var(--color-ink-2); }
.tone-dark .brand-name, .tone-dark .heading-title { color: var(--color-accent-ink); }
.tone-dark .brand-name text, .tone-dark .heading-description { color: var(--color-muted); }
.tone-dark .household-picker { border-color: var(--color-ink-2); background: var(--color-ink-2); color: var(--color-paper); }
.tone-dark .heading-title { flex: initial; color: var(--color-accent-ink); }
</style>
