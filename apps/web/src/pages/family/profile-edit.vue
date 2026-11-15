<script lang="ts">
import { defineComponent } from 'vue'

export default defineComponent({
  name: 'FamilyProfileEditPage',
  onLoad(query: { id?: string }) {
    const id = query.id ? `?edit=${encodeURIComponent(query.id)}` : ''
    uni.redirectTo({ url: `/pages/family/people${id}` })
  },
})
</script>

<template><view class="redirect">正在打开人员档案</view></template>
<style scoped>
.redirect { padding: 48px 16px; color: var(--color-muted); text-align: center; }
</style>
