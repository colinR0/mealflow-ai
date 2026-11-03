<script lang="ts">
import { defineComponent } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'

export default defineComponent({
  name: 'AccountChip',
  components: { ArrowDown },
  emits: ['command'],
  data() {
    return { displayName: 'admin', initial: 'A' }
  },
  methods: {
    onCommand(command: string) {
      this.$emit('command', command)
    },
  },
})
</script>

<template>
  <el-dropdown trigger="hover" @command="onCommand">
    <div class="user-chip">
      <el-avatar :size="30" class="user-avatar">{{ initial }}</el-avatar>
      <span class="user-name">{{ displayName }}</span>
      <el-icon class="user-caret"><ArrowDown /></el-icon>
    </div>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item disabled>当前版本 v1.2</el-dropdown-item>
        <el-dropdown-item divided command="password">修改密码</el-dropdown-item>
        <el-dropdown-item command="logout">退出登录</el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
.user-chip {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 5px 12px 5px 6px;
  border: 1px solid var(--border, #e4e7ed);
  background: #fff;
  border-radius: 999px;
  cursor: pointer;
  transition:
    box-shadow 0.2s ease,
    border-color 0.2s ease;
  outline: none;
}
.user-chip:hover {
  border-color: #bcd3cc;
  box-shadow: 0 4px 14px rgba(33, 60, 54, 0.12);
}
.user-avatar {
  background: #0f4c45;
  color: #fff;
  font-weight: 700;
}
.user-name {
  font-size: 13.5px;
  color: var(--ink, #1d2b28);
}
.user-caret {
  font-size: 12px;
  color: #8a9a94;
}
</style>
