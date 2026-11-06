<script lang="ts">
import { defineComponent } from 'vue'
import { login, restoreSession, session } from '../../shared/session'

export default defineComponent({
  name: 'LoginPage',
  data() { return { session, phone: '', password: '', busy: false, error: '' } },
  computed: { validPhone(): boolean { return /^1[3-9][0-9]{9}$/.test(this.phone) } },
  methods: {
    async signIn() {
      if (this.busy || !this.validPhone || !this.password) return
      this.busy = true
      this.error = ''
      try {
        await login(this.phone, this.password)
        uni.switchTab({ url: '/pages/fridge/index' })
      } catch (error) {
        // Keep the login form open so authentication or provisioning errors are actionable.
        this.error = error instanceof Error ? error.message : '登录失败'
      } finally {
        this.password = ''
        this.busy = false
      }
    },
    async retrySession() {
      this.busy = true
      this.error = ''
      await restoreSession()
      this.busy = false
      if (session.restoreError) this.error = session.restoreError
      else if (session.userId) uni.switchTab({ url: '/pages/fridge/index' })
    },
  },
})
</script>

<template>
  <view class="login-page">
    <view class="intro"><text class="logo">▣</text><text class="title">智慧冰箱</text><text class="subtitle">登录后查看食材、采购清单与饮食助手</text></view>
    <view class="form-card">
      <text class="form-title">手机号登录</text>
      <text v-if="session.restoreError" class="error">{{ session.restoreError }}</text>
      <button v-if="session.restoreError" class="retry" :disabled="busy" @tap="retrySession">重试恢复登录</button>
      <text class="label">手机号</text><input v-model="phone" type="number" maxlength="11" placeholder="请输入 11 位手机号" />
      <text class="label">密码</text><input v-model="password" password maxlength="256" placeholder="请输入密码" />
      <text v-if="error" class="error">{{ error }}</text>
      <button class="submit" :disabled="busy || !validPhone || !password" @tap="signIn">{{ busy ? '正在登录…' : '登录' }}</button>
      <text class="hint">仅支持已开通的手机号账号</text>
    </view>
  </view>
</template>

<style scoped lang="scss">
.login-page { min-height: 100vh; padding: calc(var(--status-bar-height, 0px) + 90rpx) 40rpx 50rpx; background: linear-gradient(180deg, #e8f2ff, #f6f9fe 45%); box-sizing: border-box; }
.intro { display: flex; flex-direction: column; align-items: center; margin-bottom: 70rpx; color: #20344f; }.logo { display: flex; align-items: center; justify-content: center; width: 108rpx; height: 108rpx; border-radius: 30rpx; background: #3478f6; color: white; font-size: 58rpx; }.title { margin-top: 24rpx; font-size: 44rpx; font-weight: 700; }.subtitle { margin-top: 12rpx; color: #6c809b; font-size: 24rpx; }
.form-card { padding: 40rpx; border-radius: 30rpx; background: #fff; box-shadow: 0 14rpx 40rpx #345b8c12; }.form-title { display: block; margin-bottom: 36rpx; font-size: 34rpx; font-weight: 650; color: #20344f; }.label { display: block; margin: 26rpx 0 12rpx; color: #40536d; font-size: 26rpx; }.form-card input { height: 88rpx; padding: 0 24rpx; border: 1rpx solid #dde7f3; border-radius: 16rpx; background: #f9fbff; font-size: 28rpx; }.submit { margin: 44rpx 0 20rpx; border-radius: 16rpx; background: #3478f6; color: #fff; font-size: 30rpx; }.submit[disabled] { opacity: .45; }.hint { display: block; text-align: center; color: #8999ad; font-size: 22rpx; }.error { display: block; margin-top: 20rpx; color: #c0392b; font-size: 24rpx; }.retry { margin-top: 18rpx; color: #3478f6; background: #edf4ff; font-size: 24rpx; }
</style>
