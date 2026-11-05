<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'

export default defineComponent({
  name: 'DevicePage',
  components: { SubpageHeader },
  data() { return { zones: ['冷藏室', '冷冻室', '变温室'], periods: ['近 24 小时', '近 7 天'], period: '近 24 小时' } },
})
</script>

<template>
  <view class="device-page">
    <SubpageHeader title="设备与环境" />
    <view class="content">
      <view class="panel overview"><view class="symbol">▥</view><view><text class="title">连接你的家庭冰箱</text><text class="muted">设备绑定与传感器服务尚未接入</text></view></view>
      <text class="intro">接入设备后，可在这里查看分区环境、连接状态和告警记录。</text>
      <view class="heading"><text class="title">分区环境</text><text class="muted">暂无采集数据</text></view>
      <view class="zones"><view v-for="zone in zones" :key="zone" class="panel zone"><text class="zone-name">{{ zone }}</text><view class="metrics"><view><text class="value">— <text>°C</text></text><text class="muted">温度</text></view><view><text class="value">— <text>%</text></text><text class="muted">湿度</text></view></view><text class="zone-footer">等待设备上传数据</text></view></view>
      <view class="panel history"><text class="title">环境变化</text><view class="periods"><button v-for="item in periods" :key="item" :class="{ selected: period === item }" @tap="period = item">{{ item }}</button></view><view class="chart-empty"><text class="chart-icon">⌁</text><text>暂无{{ period }}的温湿度记录</text><text class="muted">收到真实采集数据后展示变化曲线</text></view></view>
      <view class="panel history"><text class="title">设备告警</text><text class="intro">尚未接入告警服务，无法判断设备是否存在异常。</text></view>
      <text class="footnote">设备离线不等于停电。缺少离线期间的采集记录时，无法判断食材储存温度及安全状况。</text>
    </view>
  </view>
</template>

<style scoped lang="scss">
.device-page { min-height: 100vh; background: #f3f7fc; }.content { padding: 20px 16px 36px; }.panel { padding: 18px; border-radius: 18px; background: #fff; }.overview { display: flex; align-items: center; gap: 14px; }.symbol { padding: 8px 14px; border-radius: 12px; background: #edf4ff; color: #3478f6; font-size: 34px; }.title { display: block; color: #20344f; font-size: 17px; font-weight: 600; }.muted { display: block; margin-top: 6px; color: #7b8ba2; font-size: 12px; line-height: 1.6; }.intro { display: block; margin: 14px 0 24px; color: #657a94; font-size: 13px; line-height: 1.8; }.heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }.zones { display: flex; flex-wrap: wrap; gap: 10px; }.zone { flex: 1 1 140px; }.zone-name { font-size: 14px; font-weight: 600; color: #334155; }.metrics { display: flex; gap: 28px; margin-top: 20px; }.value { color: #3478f6; font-size: 30px; }.value text { font-size: 13px; }.zone-footer { display: block; margin-top: 16px; border-top: 1px solid #edf2f7; padding-top: 12px; color: #94a3b8; font-size: 12px; }.history { margin-top: 18px; }.periods { display: flex; gap: 8px; margin-top: 16px; }.periods button { margin: 0; padding: 0 14px; background: #f3f7fc; color: #64748b; font-size: 12px; line-height: 34px; border-radius: 9px; }.periods .selected { background: #e8f0ff; color: #2563eb; }.chart-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 170px; color: #718096; font-size: 13px; }.chart-icon { color: #b9cde7; font-size: 50px; }
</style>
