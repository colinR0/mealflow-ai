<script lang="ts">
import { defineComponent } from 'vue'
import BottomNav from '../../components/BottomNav.vue'

export default defineComponent({
  name: 'AssistantPage',
  components: { BottomNav },
})
</script>

<template>
  <view class="assistant-rebuild">
    <view class="notice" role="status">
      <text class="title">助手正在重建，暂不可用</text>
      <text class="description">正在升级菜谱、库存与家庭饮食信息工具，完成后恢复对话。</text>
      <text class="description">冰箱库存、人员信息和采购清单仍可正常使用。</text>
    </view>
    <BottomNav active="assistant" />
  </view>
</template>

<style scoped>
.assistant-rebuild { min-height: 100vh; box-sizing: border-box; padding: 90px 24px 110px; background: #f2f7fc; }
.notice { padding: 28px 22px; border-radius: 18px; background: #fff; }
.title { display: block; margin-bottom: 18px; color: #183750; font-size: 21px; font-weight: 600; }
.description { display: block; margin-top: 12px; color: #64748b; font-size: 15px; line-height: 1.8; }
</style>
