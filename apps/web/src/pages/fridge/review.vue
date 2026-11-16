<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import { API_BASE } from '../../shared/config'
import { session } from '../../shared/session'
import { request } from '../../shared/transport'

interface Detection {
  id: string
  name: string
  prompt: string
  score: number
  box: number[]
  needs_review: boolean
  note: string
  kept: boolean
}
interface Observation {
  id: string
  status: string
  image_url: string
  items: Detection[]
}

export default defineComponent({
  name: 'FridgeReviewPage',
  components: { SubpageHeader },
  data() {
    return {
      observationId: '',
      observation: null as Observation | null,
      imagePath: '',
      loading: false,
      busy: false,
      error: '',
      saved: false,
    }
  },
  computed: {
    keptCount(): number {
      return this.observation?.items.filter((item) => item.kept && item.name.trim()).length || 0
    },
  },
  onLoad(options: { id?: string }) {
    this.observationId = options?.id || ''
    void this.load()
  },
  methods: {
    async load() {
      if (!session.householdId) { this.error = '尚未选择家庭'; return }
      this.loading = true
      this.error = ''
      try {
        const id = this.observationId || await this.latestPendingId()
        if (!id) { this.observation = null; return }
        this.observationId = id
        const row = await request<Observation>(`/households/${session.householdId}/fridge-observations/${id}`)
        this.observation = {
          ...row,
          items: row.items.map((item) => ({ ...item, kept: item.kept !== false })),
        }
        this.loadImage(row.image_url)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '识别结果加载失败'
      } finally {
        this.loading = false
      }
    },
    async latestPendingId(): Promise<string> {
      const listed = await request<{ items: Observation[] }>(
        `/households/${session.householdId}/fridge-observations?status=pending`,
      )
      return listed.items[0]?.id || ''
    },
    loadImage(url: string) {
      uni.downloadFile({
        url: `${API_BASE}${url}`,
        header: session.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {},
        success: (result) => {
          if (result.statusCode === 200) this.imagePath = result.tempFilePath
        },
      })
    },
    boxStyle(item: Detection) {
      const [x1, y1, x2, y2] = item.box
      return {
        left: `${x1 * 100}%`,
        top: `${y1 * 100}%`,
        width: `${(x2 - x1) * 100}%`,
        height: `${(y2 - y1) * 100}%`,
      }
    },
    async confirm() {
      if (!this.observation || this.busy || this.saved || !this.keptCount) return
      this.busy = true
      this.error = ''
      try {
        await request(
          `/households/${session.householdId}/fridge-observations/${this.observation.id}/confirm`,
          {
            items: this.observation.items.map((item) => ({
              id: item.id,
              name: item.name.trim(),
              kept: item.kept,
            })),
          },
        )
        this.saved = true
        uni.showToast({ title: '已加入库存', icon: 'success' })
        setTimeout(() => uni.navigateBack({ delta: 1 }), 400)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '确认失败'
      } finally {
        this.busy = false
      }
    },
    async discard() {
      if (!this.observation || this.busy) return
      this.busy = true
      this.error = ''
      try {
        await request(
          `/households/${session.householdId}/fridge-observations/${this.observation.id}/discard`,
          {},
        )
        uni.navigateBack({ delta: 1 })
      } catch (error) {
        this.error = error instanceof Error ? error.message : '放弃失败'
        this.busy = false
      }
    },
  },
})
</script>

<template>
  <view class="review-page">
    <SubpageHeader title="识别结果核对" />
    <view v-if="loading" class="empty-card"><text>正在读取识别结果</text></view>
    <view v-else-if="!observation" class="empty-card">
      <text class="empty-title">暂无待核对的识别结果</text>
      <text>从冰箱画面上传一张照片后，识别到的食材会出现在这里。确认前不会写入库存。</text>
    </view>
    <view v-else class="review-body">
      <view class="frame">
        <image v-if="imagePath" class="shot" mode="widthFix" :src="imagePath" />
        <view v-else class="shot waiting">照片加载中</view>
        <view
          v-for="item in observation.items"
          :key="item.id"
          class="box"
          :class="{ muted: !item.kept, caution: item.needs_review }"
          :style="boxStyle(item)"
        >
          <text class="box-label">{{ item.name }}</text>
        </view>
      </view>
      <view v-if="!observation.items.length" class="empty-card inline">
        <text class="empty-title">这张照片没有识别到食材</text>
        <text>可以放弃这张照片，或换一张更清晰的再试。</text>
      </view>
      <view v-for="item in observation.items" :key="item.id" class="item-card">
        <view class="item-head">
          <text :class="{ selected: item.kept }" @tap="item.kept = !item.kept">{{ item.kept ? '保留' : '已划掉' }}</text>
          <text class="score">{{ Math.round(item.score * 100) }}%</text>
        </view>
        <input v-model="item.name" :disabled="!item.kept || saved" maxlength="40" />
        <text v-if="item.needs_review" class="caution-copy">{{ item.note || '置信度偏低，请核对是否看错' }}</text>
      </view>
      <view v-if="error" class="error">{{ error }}</view>
      <view class="hint">确认后数量记为未知，不会按框的个数写成 1。</view>
      <view class="actions">
        <button class="secondary" :disabled="busy || saved" @tap="discard">放弃</button>
        <button class="primary" :disabled="busy || saved || !keptCount" :loading="busy" @tap="confirm">{{ saved ? '已入库' : '确认加入库存' }}</button>
      </view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.review-page { min-height: 100vh; background: #f3f4f6; padding-bottom: calc(24rpx + env(safe-area-inset-bottom)); }
.empty-card { margin: 24rpx 32rpx; padding: 48rpx 32rpx; border-radius: 24rpx; background: #fff; color: #718096; font-size: 25rpx; line-height: 1.7; text-align: center; }
.empty-card.inline { margin: 24rpx 0 0; }
.empty-title { display: block; margin-bottom: 16rpx; color: #263449; font-size: 30rpx; font-weight: 650; }
.review-body { padding: 24rpx 32rpx; }
.frame { position: relative; overflow: hidden; border-radius: 24rpx; background: #111827; }
.shot { display: block; width: 100%; }
.waiting { padding: 80rpx 0; color: #d1d5db; text-align: center; }
.box { position: absolute; box-sizing: border-box; border: 3rpx solid #60a5fa; }
.box.caution { border-color: #f59e0b; }
.box.muted { opacity: 0.25; }
.box-label { position: absolute; top: 0; left: 0; max-width: 100%; padding: 2rpx 8rpx; background: #1d4ed8; color: #fff; font-size: 20rpx; }
.box.caution .box-label { background: #b45309; }
.item-card { margin-top: 18rpx; padding: 22rpx; border-radius: 18rpx; background: #fff; }
.item-head { display: flex; justify-content: space-between; margin-bottom: 12rpx; color: #64748b; font-size: 24rpx; }
.item-head .selected { color: #1d4ed8; font-weight: 650; }
.item-card input { height: 72rpx; padding: 0 16rpx; border-radius: 10rpx; background: #f3f4f6; font-size: 28rpx; }
.caution-copy, .hint, .error { display: block; margin-top: 12rpx; font-size: 23rpx; line-height: 1.5; }
.caution-copy { color: #92400e; }
.hint { margin-top: 20rpx; color: #8391a7; }
.error { color: #b91c1c; }
.actions { display: flex; gap: 16rpx; margin-top: 20rpx; }
.secondary, .primary { flex: 1; margin: 0; border-radius: 12rpx; font-size: 28rpx; line-height: 84rpx; }
.secondary { background: #fff; color: #334155; }
.primary { background: #2563eb; color: #fff; }
</style>
