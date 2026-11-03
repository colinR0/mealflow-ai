<script lang="ts">
import { defineComponent } from 'vue'
/**
 * 登录页占位(V2.1 实现):表单结构先行,接后端鉴权后填充逻辑。
 * 预期:账号密码登录 → localStorage 存 token → 路由守卫放行 /admin。
 */
export default defineComponent({ name: 'LoginView' })
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <template #header>
        <div class="login-head">
          <span class="brand-mark">食</span>
          <div>
            <strong>食序 MealFlow</strong>
            <small>家庭智能配餐 Agent</small>
          </div>
        </div>
      </template>
      <el-form label-position="top" disabled>
        <el-form-item label="账号">
          <el-input placeholder="手机号 / 邮箱" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input type="password" placeholder="请输入密码" show-password />
        </el-form-item>
        <el-button type="primary" class="login-btn" disabled>登 录</el-button>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        title="登录功能规划中(V2.1 上线)"
        description="当前为演示模式,点击下方直接进入工作台。"
      />
      <RouterLink to="/admin/agent">
        <el-button class="login-btn" plain>先去体验工作台 →</el-button>
      </RouterLink>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f2f5f3;
}
.login-card {
  width: 380px;
}
.login-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.login-head .brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: #0f4c45;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}
.login-head small {
  display: block;
  color: #909399;
}
.login-btn {
  width: 100%;
  margin-top: 8px;
}
</style>
