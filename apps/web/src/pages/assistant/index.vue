<script lang="ts">
import { defineComponent } from 'vue'

export default defineComponent({
  name: 'AssistantPage',
})
</script>

<template>
  <view class="assistant-rebuild">
    <view class="notice" role="status">
      <text class="title">助手正在重建，暂不可用</text>
      <text class="description">正在升级菜谱、库存与家庭饮食信息工具，完成后恢复对话。</text>
      <text class="description">冰箱库存、人员信息和采购清单仍可正常使用。</text>
    </view>
  </view>
</template>

<style scoped>
.assistant-rebuild {
  min-height: 100vh;
  box-sizing: border-box;
  padding: calc(var(--status-bar-height, 0px) + 28px) 24px 96px;
  background: linear-gradient(180deg, #d7ecff 0%, #f5f9ff 42%, #f7fbff 100%);
}
.notice { padding: 28px 22px; border-radius: 20px; background: #fff; box-shadow: 0 8px 24px rgba(47, 120, 246, 0.08); }
.title { display: block; margin-bottom: 18px; color: #1c2b45; font-size: 21px; font-weight: 600; }
.description { display: block; margin-top: 12px; color: #7d8da6; font-size: 15px; line-height: 1.8; }
</style>
