<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import { session } from '../../shared/session'

export default defineComponent({
  name: 'MyHouseholdPage',
  components: { SubpageHeader },
  data() { return { session } },
  computed: { currentHousehold(): string { return session.households.find((item) => item.id === session.householdId)?.name || '我的家庭' } },
})
</script>

<template>
  <view class="page">
    <SubpageHeader title="我的家庭" />
    <view class="card"><text class="label">当前家庭</text><text class="name">{{ currentHousehold }}</text><text class="hint">账号：{{ session.phoneMasked || '已登录' }}</text></view>
    <view class="card"><text class="name">家庭共享</text><text class="copy">家庭成员可共享冰箱画面、库存和采购信息，并共同维护人员饮食档案。</text></view>
    <view class="card"><text class="name">邀请加入家庭</text><text class="copy">手机号账号关联与邀请加入流程待接入。当前仅展示功能框架。</text><button disabled>邀请成员 · 待接入</button></view>
  </view>
</template>

<style scoped lang="scss">
.page { min-height: 100vh; background: #f3f7fc; }.card { margin: 24rpx 32rpx; padding: 34rpx; border-radius: 24rpx; background: #fff; }.label, .hint { display: block; color: #8494aa; font-size: 24rpx; }.name { display: block; margin: 12rpx 0; color: #243952; font-size: 32rpx; font-weight: 650; }.copy { display: block; color: #647891; font-size: 25rpx; line-height: 1.7; }.card button { margin-top: 24rpx; border-radius: 14rpx; color: #8293a8; background: #edf1f6; font-size: 25rpx; }
.page { min-height: 0; }
</style>
