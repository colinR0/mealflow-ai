<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import { session } from '../../shared/session'
import { ApiError, requestBytes } from '../../shared/transport'

interface Observation { id: string }

export default defineComponent({
  name: 'FridgeCameraPage',
  components: { SubpageHeader },
  data() {
    return { busy: false, error: '', preview: '' }
  },
  methods: {
    choose() {
      if (this.busy) return
      uni.chooseImage({
        count: 1,
        sizeType: ['original'],
        sourceType: ['album', 'camera'],
        success: (result) => { void this.recognize(result.tempFilePaths[0]) },
      })
    },
    async recognize(path: string) {
      if (!session.householdId) { this.error = '尚未选择家庭'; return }
      this.busy = true
      this.error = ''
      this.preview = path
      try {
        const bytes = await this.readPhoto(path)
        const contentType = new Uint8Array(bytes.slice(0, 8))[0] === 0x89 ? 'image/png' : 'image/jpeg'
        const observation = await requestBytes<Observation>(
          `/households/${session.householdId}/fridge-observations`,
          bytes,
          contentType,
        )
        uni.navigateTo({ url: `/pages/fridge/review?id=${observation.id}` })
      } catch (error) {
        this.error = error instanceof ApiError || error instanceof Error ? error.message : '识别未完成'
      } finally {
        this.busy = false
      }
    },
    readPhoto(path: string): Promise<ArrayBuffer> {
      return new Promise((resolve, reject) => {
        const manager = uni.getFileSystemManager?.()
        if (!manager) {
          fetch(path).then((response) => response.arrayBuffer()).then(resolve, () => reject(new Error('照片读取失败')))
          return
        }
        manager.readFile({
          filePath: path,
          success: (result) => resolve(result.data as ArrayBuffer),
          fail: () => {
            fetch(path).then((response) => response.arrayBuffer()).then(resolve, () => reject(new Error('照片读取失败')))
          },
        })
      })
    },
  },
})
</script>

<template>
  <view class="camera-page">
    <SubpageHeader title="冰箱画面" />
    <view class="camera-content">
      <view v-if="preview" class="picture preview"><image class="shot" mode="aspectFit" :src="preview" /></view>
      <view v-else class="picture"><text class="picture-icon">▣</text><text>还没有照片</text></view>
      <view class="state-card">
        <text class="state-title">{{ busy ? '正在识别' : '上传一张冰箱照片' }}</text>
        <text class="state-copy">{{ busy ? '本地模型正在按食材逐个查看，可能需要一两分钟。' : '选择相册或现场拍一张。这里还不是实时监控。识别结果需要核对后才会进入库存。' }}</text>
        <button class="primary" :disabled="busy" :loading="busy" @tap="choose">{{ busy ? '识别中' : '选择照片' }}</button>
      </view>
      <view v-if="error" class="error">{{ error }}</view>
      <view class="hint">数量不会自动填写。确认前可以改名或划掉看错的食材。</view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.camera-page { background: #f3f4f6; }
.camera-content { padding: 24rpx 32rpx calc(32rpx + env(safe-area-inset-bottom)); }
.picture { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18rpx; aspect-ratio: 4 / 3; border-radius: 24rpx; background: #e8edf3; color: #718096; font-size: 28rpx; overflow: hidden; }
.preview { background: #111827; }
.shot { width: 100%; height: 100%; }
.picture-icon { color: #9caec3; font-size: 70rpx; }
.state-card { margin-top: 24rpx; padding: 30rpx; border-radius: 22rpx; background: #fff; }
.state-title, .state-copy { display: block; }
.state-title { color: #263449; font-size: 30rpx; font-weight: 650; }
.state-copy { margin-top: 12rpx; color: #718096; font-size: 25rpx; line-height: 1.6; }
.primary { margin: 28rpx 0 0; border-radius: 12rpx; background: #2563eb; color: #fff; font-size: 28rpx; line-height: 84rpx; }
.hint, .error { margin-top: 24rpx; font-size: 23rpx; line-height: 1.6; }
.hint { color: #8391a7; }
.error { color: #b91c1c; }
</style>
