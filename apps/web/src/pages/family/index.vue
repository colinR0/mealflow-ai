<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import manifest from '../../manifest.json'
import { logout, session } from '../../shared/session'

export default defineComponent({
  name: 'MyPage',
  components: { AppHeader },
  data() { return { session, version: manifest.versionName, busy: false } },
  methods: {
    openPeople() { uni.navigateTo({ url: '/pages/family/people' }) },
    openHousehold() { uni.navigateTo({ url: '/pages/family/household' }) },
    signOut() {
      uni.showModal({ title: '退出登录', content: '确定退出当前账号吗？', success: (result) => {
        if (!result.confirm || this.busy) return
        this.busy = true
        void logout().then(() => uni.reLaunch({ url: '/pages/auth/login' }))
          .finally(() => { this.busy = false })
      } })
    },
  },
})
</script>

<template>
  <view class="my-page">
    <AppHeader eyebrow="" title="我的" />
    <view class="account"><view class="avatar">我</view><view><text class="account-title">我的账号</text><text class="account-phone">{{ session.phoneMasked || '已登录' }}</text></view></view>
    <view v-if="session.households.length > 1" class="legacy-note">此账号有多个历史家庭，当前沿用上次选择的资料；暂不合并数据。</view>
    <view class="menu-group">
      <button class="menu-row" @tap="openPeople"><text class="menu-icon">♙</text><text class="menu-label">人员信息</text><text class="arrow">›</text></button>
      <button class="menu-row" @tap="openHousehold"><text class="menu-icon">⌂</text><text class="menu-label">我的家庭</text><text class="arrow">›</text></button>
    </view>
    <view class="menu-group">
      <view class="menu-row"><text class="menu-icon">ⓘ</text><text class="menu-label">当前版本</text><text class="value">v{{ version }}</text></view>
      <button class="menu-row logout" :disabled="busy" @tap="signOut"><text class="menu-icon">↪</text><text class="menu-label">退出登录</text><text class="arrow">›</text></button>
    </view>
  </view>
</template>

<style scoped lang="scss">
.my-page { min-height: 100vh; background: #f3f7fc; }.account { display: flex; align-items: center; gap: 24rpx; margin: 28rpx 32rpx 36rpx; padding: 36rpx 32rpx; border-radius: 28rpx; background: linear-gradient(120deg, #e6f0ff, #fff); }.avatar { display: flex; align-items: center; justify-content: center; width: 104rpx; height: 104rpx; border-radius: 50%; background: #3478f6; color: #fff; font-size: 38rpx; }.account-title { display: block; color: #20344f; font-size: 34rpx; font-weight: 650; }.account-phone { display: block; margin-top: 8rpx; color: #67809f; font-size: 25rpx; }.legacy-note { margin: 0 32rpx 24rpx; color: #9a651e; font-size: 24rpx; line-height: 1.6; }.menu-group { margin: 0 32rpx 28rpx; overflow: hidden; border-radius: 24rpx; background: #fff; }.menu-row { display: flex; align-items: center; width: 100%; min-height: 100rpx; margin: 0; padding: 0 30rpx; border-bottom: 1rpx solid #edf1f6; border-radius: 0; background: #fff; color: #273b55; text-align: left; font-size: 28rpx; line-height: 1.4; }.menu-row:last-child { border-bottom: none; }.menu-row::after { border: none; }.menu-icon { width: 56rpx; color: #3478f6; font-size: 34rpx; }.menu-label { flex: 1; }.value { color: #8998ac; font-size: 24rpx; }.arrow { color: #a4b1c1; font-size: 38rpx; }.logout .menu-label { color: #c25050; }
.my-page { min-height: 0; }
</style>
