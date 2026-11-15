<script lang="ts">
import { defineComponent } from 'vue'
export default defineComponent({ name: 'SurfaceCard', props: { title: { type: String, required: true }, action: { type: String, default: '' } }, emits: ['action'] })
</script>

<template><view class="surface-card"><view class="surface-heading"><text>{{ title }}</text><text v-if="action" class="surface-action" @tap="$emit('action')">{{ action }}</text></view><slot /></view></template>

<style lang="scss">
.surface-card { margin: 0 var(--page-gap) 26rpx; padding: 30rpx; border: 1px solid var(--color-rule); border-radius: var(--radius-card); background: var(--color-surface); box-shadow: var(--shadow-card); }
.surface-heading { display: flex; justify-content: space-between; align-items: center; gap: 16rpx; margin-bottom: 20rpx; color: var(--color-ink); font-size: 30rpx; font-weight: 650; }
.surface-action { display: flex; align-items: center; min-height: 88rpx; padding: 20rpx 16rpx; color: var(--color-accent-deep); font-size: 24rpx; font-weight: 400; flex-shrink: 0; }
</style>
