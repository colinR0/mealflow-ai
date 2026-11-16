<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import { API_BASE } from '../../shared/config'
import { session } from '../../shared/session'
import { request } from '../../shared/transport'

interface Dish { id: string; name: string; note: string }
interface Photo { id: string; url: string; content_type: string }
interface Comment { id: string; body: string; created_at: string; author: { display_name: string } }
interface Log {
  id: string; title: string; cooked_at: string; notes: string; can_edit: boolean
  allow_assistant_citation: boolean; version: number; dishes: Dish[]; photos: Photo[]; comments: Comment[]
  creator: { display_name: string }
}

export default defineComponent({
  name: 'CookingLogDetailPage',
  components: { SubpageHeader },
  data() {
    return {
      logId: '', log: null as Log | null, loading: false, error: '', saving: false, editing: false,
      commentBody: '', form: { title: '', notes: '', allow_assistant_citation: false },
      photoPaths: {} as Record<string, string>,
    }
  },
  computed: {
    base(): string { return `/households/${session.householdId}/cooking-logs/${this.logId}` },
  },
  onLoad(options: { id?: string }) { this.logId = options.id || ''; void this.load() },
  methods: {
    load() {
      if (!this.logId) { this.error = '记录不存在'; return }
      this.loading = true
      this.error = ''
      return request<Log>(this.base).then((log) => {
        this.log = log
        this.form = { title: log.title, notes: log.notes, allow_assistant_citation: log.allow_assistant_citation }
        this.loadPhotos(log)
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.loading = false })
    },
    loadPhotos(log: Log) {
      for (const photo of log.photos) {
        if (this.photoPaths[photo.id]) continue
        uni.downloadFile({
          url: `${API_BASE}${photo.url}`,
          header: session.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {},
          success: (result) => {
            if (result.statusCode === 200) this.photoPaths[photo.id] = result.tempFilePath
          },
        })
      }
    },
    setCitation(event: Event) {
      this.form.allow_assistant_citation = (event as CustomEvent<{ value: boolean }>).detail.value
    },
    preview(photoId: string) {
      const path = this.photoPaths[photoId]
      if (path) uni.previewImage({ urls: [path] })
    },
    addComment() {
      const body = this.commentBody.trim()
      if (!body || this.saving) return
      this.saving = true
      this.error = ''
      return request(`${this.base}/comments`, { body }).then(() => {
        this.commentBody = ''
        return this.load()
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.saving = false })
    },
    save() {
      if (!this.log || this.saving || !this.form.title.trim()) return
      this.saving = true
      this.error = ''
      return request<Log>(this.base, {
        expected_version: this.log.version,
        title: this.form.title.trim(),
        notes: this.form.notes,
        allow_assistant_citation: this.form.allow_assistant_citation,
      }, true, {}, 'PATCH').then((log) => {
        this.log = log
        this.editing = false
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.saving = false })
    },
  },
})
</script>

<template>
  <view class="detail-page">
    <SubpageHeader title="做饭记录" :switchable="false" />
    <view v-if="error" class="error">{{ error }}</view>
    <text v-if="loading" class="loading">正在读取记录…</text>
    <template v-else-if="log">
      <view class="card">
        <text class="title">{{ log.title }}</text>
        <text class="meta">{{ new Date(log.cooked_at).toLocaleString() }} · {{ log.creator.display_name }}</text>
        <text v-if="log.dishes.length" class="dishes">{{ log.dishes.map((dish) => dish.name).join('、') }}</text>
        <text v-if="log.notes" class="notes">{{ log.notes }}</text>
        <view v-if="log.photos.length" class="photos">
          <image v-for="photo in log.photos" :key="photo.id" class="photo" mode="aspectFill" :src="photoPaths[photo.id]" @tap="preview(photo.id)" />
        </view>
        <view v-if="log.can_edit" class="edit-head">
          <text class="citation-state">{{ log.allow_assistant_citation ? '助手已获授权引用' : '助手默认不引用' }}</text>
          <button class="text-button" @tap="editing = !editing">{{ editing ? '取消编辑' : '编辑' }}</button>
        </view>
        <view v-if="editing" class="edit-form">
          <input v-model="form.title" class="input" maxlength="120" />
          <textarea v-model="form.notes" class="textarea" maxlength="2000" placeholder="备注" />
          <label class="citation"><switch :checked="form.allow_assistant_citation" @change="setCitation" />允许助手在回答中引用这条记录</label>
          <button class="primary" :disabled="saving" @tap="save">{{ saving ? '保存中…' : '保存修改' }}</button>
        </view>
      </view>
      <view class="comments">
        <text class="section-title">评论 {{ log.comments.length }}</text>
        <view v-for="comment in log.comments" :key="comment.id" class="comment">
          <text class="comment-head">{{ comment.author.display_name }} · {{ new Date(comment.created_at).toLocaleString() }}</text>
          <text class="comment-body">{{ comment.body }}</text>
        </view>
        <text v-if="!log.comments.length" class="muted">还没有评论，来说说这顿饭吧。</text>
        <textarea v-model="commentBody" class="textarea" maxlength="500" placeholder="写评论（全家可见）" />
        <button class="primary" :disabled="saving || !commentBody.trim()" @tap="addComment">{{ saving ? '发送中…' : '发表评论' }}</button>
      </view>
    </template>
  </view>
</template>
<style scoped lang="scss">
.detail-page { min-height: 100vh; background: #f3f7fc; padding-bottom: 40rpx; }
.card, .comments { margin: 24rpx 28rpx; padding: 26rpx; background: #fff; border-radius: 22rpx; }.title { display: block; color: #20344f; font-size: 36rpx; font-weight: 650; }.meta, .dishes, .notes { display: block; margin-top: 12rpx; color: #8998ac; font-size: 23rpx; line-height: 1.7; }.dishes { color: #526780; }.notes { color: #61758f; white-space: pre-wrap; }
.photos { display: flex; flex-wrap: wrap; gap: 12rpx; margin-top: 18rpx; }.photo { width: 210rpx; height: 210rpx; border-radius: 14rpx; background: #eef2f7; }
.edit-head { display: flex; align-items: center; margin-top: 20rpx; padding-top: 18rpx; border-top: 1rpx solid #edf1f6; }.citation-state { flex: 1; color: #6d8fb4; font-size: 22rpx; }.text-button { margin: 0; padding: 0; color: #3478f6; font-size: 23rpx; line-height: 1.6; }
.edit-form { margin-top: 16rpx; }.input, .textarea { box-sizing: border-box; width: 100%; margin-bottom: 14rpx; padding: 16rpx 20rpx; background: #f4f7fb; border-radius: 14rpx; color: #283d56; font-size: 25rpx; line-height: 1.6; }.input { height: 88rpx; padding: 0 20rpx; }.textarea { height: 150rpx; }.citation { display: flex; align-items: center; gap: 4rpx; color: #61758f; font-size: 22rpx; line-height: 1.6; }.citation switch { transform: scale(.7); }
.primary { margin: 8rpx 0 0; padding: 0 24rpx; line-height: 64rpx; border-radius: 16rpx; background: #3478f6; color: #fff; font-size: 24rpx; }.section-title { display: block; margin-bottom: 14rpx; color: #20344f; font-size: 28rpx; font-weight: 650; }.comment { padding: 14rpx 0; border-bottom: 1rpx solid #f0f3f7; }.comment-head { display: block; color: #7f90a5; font-size: 21rpx; }.comment-body { display: block; margin-top: 6rpx; color: #3c4e65; font-size: 25rpx; line-height: 1.7; white-space: pre-wrap; }.muted, .loading { display: block; padding: 20rpx; color: #8a98aa; font-size: 23rpx; text-align: center; }.error { margin: 20rpx 28rpx; color: #b45f4e; font-size: 24rpx; line-height: 1.7; }
</style>