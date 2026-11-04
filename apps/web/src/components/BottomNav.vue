<script lang="ts">
import { defineComponent } from 'vue'

export default defineComponent({
  name: 'BottomNav',
  props: { active: { type: String, required: true } },
  methods: {
    open(path: string) { uni.switchTab({ url: path }) },
  },
})
</script>

<template>
  <view class="bottom-nav">
    <view v-for="item in [{ id: 'fridge', label: '冰箱', path: '/pages/fridge/index', icon: '▣' }, { id: 'assistant', label: '助手', path: '/pages/assistant/index', icon: '▤' }, { id: 'shopping', label: '采购', path: '/pages/shopping/index', icon: '♧' }, { id: 'family', label: '家庭', path: '/pages/family/index', icon: '♙' }]" :key="item.id" class="nav-item" :class="{ active: active === item.id }" @tap="open(item.path)">
      <text class="nav-icon">{{ item.icon }}</text><text>{{ item.label }}</text>
    </view>
  </view>
</template>

<style lang="scss">
.bottom-nav { display: flex; flex-shrink: 0; padding: 10px 8px calc(10px + env(safe-area-inset-bottom)); border-top: 1px solid #e5e7eb; background: #fff; }
.nav-item { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 54px; gap: 3px; color: #71717a; font-size: 12px; }
.nav-icon { font-size: 23px; line-height: 24px; }
.nav-item.active { color: #2563eb; }
</style>
