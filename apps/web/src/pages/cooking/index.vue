<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import { session } from '../../shared/session'
import { request } from '../../shared/transport'

interface Dish { id: string; name: string; note: string }
interface Log {
  id: string; title: string; cooked_at: string; notes: string; can_edit: boolean
  allow_assistant_citation: boolean; version: number; dishes: Dish[]; comment_count: number
  creator: { id: string; display_name: string }
}
const today = () => new Date().toISOString().slice(0, 10)

export default defineComponent({
  name: 'CookingLogsPage',
  components: { SubpageHeader },
  data() {
    return {
      session, logs: [] as Log[], loading: false, error: '', mine: false, creating: false, saving: false,
      form: { title: '', cooked_at: today(), notes: '', dishes: '', allow_assistant_citation: false },
    }
  },
  computed: {
    base(): string { return `/households/${session.householdId}/cooking-logs` },
  },
  watch: { 'session.householdId'() { void this.load() } },
  onShow() { void this.load() },
  methods: {
    load() {
      if (!session.householdId || this.loading) return
      this.loading = true
      this.error = ''
      return request<{ items: Log[] }>(`${this.base}?mine=${this.mine}`).then((data) => {
        this.logs = data.items
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.loading = false })
    },
    changeFilter(event: Event) { this.mine = (event as CustomEvent<{ value: boolean }>).detail.value; void this.load() },
    setCitation(event: Event) { this.form.allow_assistant_citation = (event as CustomEvent<{ value: boolean }>).detail.value },
    open(id: string) { uni.navigateTo({ url: `/pages/cooking/detail?id=${id}` }) },
    reset() { this.form = { title: '', cooked_at: today(), notes: '', dishes: '', allow_assistant_citation: false } },
    save() {
      const title = this.form.title.trim()
      if (!title || this.saving) return
      const dishes = this.form.dishes.split(/[、,，\s]+/).map((name) => name.trim()).filter(Boolean)
        .map((name) => ({ name }))
      this.saving = true
      this.error = ''
      return request<Log>(this.base, {
        title,
        cooked_at: new Date(`${this.form.cooked_at}T12:00:00`).toISOString(),
        notes: this.form.notes.trim(),
        allow_assistant_citation: this.form.allow_assistant_citation,
        dishes,
      }, true, { 'Idempotency-Key': `${Date.now()}-${Math.random().toString(36).slice(2)}` }).then((log) => {
        this.logs = [log, ...this.logs]
        this.creating = false
        this.reset()
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.saving = false })
    },
  },
})
</script>

<template>
  <view class="cooking-page">
    <SubpageHeader title="我的做饭记录" />
    <view class="toolbar">
      <label class="mine"><switch :checked="mine" @change="changeFilter" />只看我做的</label>
      <button class="primary" @tap="creating = !creating">{{ creating ? '取消' : '＋ 记一顿' }}</button>
    </view>
    <view v-if="error" class="error">{{ error }}</view>
    <view v-if="creating" class="form">
      <input v-model="form.title" class="input" maxlength="120" placeholder="这顿做了什么，例如：清蒸鲈鱼" />
      <input v-model="form.cooked_at" class="input" type="date" />
      <textarea v-model="form.dishes" class="textarea" maxlength="500" placeholder="菜品，用顿号或逗号分隔（可不填）" />
      <textarea v-model="form.notes" class="textarea" maxlength="2000" placeholder="备注：火候、家人的反馈…" />
      <label class="citation"><switch :checked="form.allow_assistant_citation" @change="setCitation" />允许助手在回答中引用这条记录</label>
      <text class="hint">默认关闭；只有你本人可以修改记录，全家都可以查看和评论。</text>
      <button class="primary save" :disabled="saving || !form.title.trim()" @tap="save">{{ saving ? '保存中…' : '保存记录' }}</button>
    </view>
    <text v-if="loading" class="loading">正在读取做饭记录…</text>
    <view v-else-if="!logs.length" class="empty"><text>还没有做饭记录</text><text>把今天做的一顿饭记下来，全家都能看到。</text></view>
    <view v-for="log in logs" :key="log.id" class="card" @tap="open(log.id)">
      <view class="card-head"><text class="title">{{ log.title }}</text><text v-if="log.allow_assistant_citation" class="badge">助手可引用</text></view>
      <text class="meta">{{ new Date(log.cooked_at).toLocaleString() }} · {{ log.creator.display_name }}</text>
      <text v-if="log.dishes.length" class="dishes">{{ log.dishes.map((dish) => dish.name).join('、') }}</text>
      <text v-if="log.notes" class="notes">{{ log.notes }}</text>
      <text class="comments">{{ log.comment_count }} 条评论{{ log.can_edit ? ' · 可编辑' : '' }}</text>
    </view>
  </view>
</template>
<style scoped lang="scss">
.cooking-page { min-height: 100vh; background: #f3f7fc; padding-bottom: 40rpx; }
.toolbar { display: flex; align-items: center; gap: 18rpx; padding: 20rpx 28rpx; }.mine { flex: 1; display: flex; align-items: center; gap: 4rpx; color: #61758f; font-size: 23rpx; }.mine switch { transform: scale(.75); }
.primary { margin: 0; padding: 0 24rpx; line-height: 64rpx; border-radius: 16rpx; background: #3478f6; color: #fff; font-size: 24rpx; }
.form, .card { margin: 0 28rpx 20rpx; padding: 24rpx; background: #fff; border-radius: 22rpx; }.input, .textarea { box-sizing: border-box; width: 100%; margin-bottom: 16rpx; padding: 16rpx 20rpx; background: #f4f7fb; border-radius: 14rpx; color: #283d56; font-size: 26rpx; line-height: 1.6; }.input { height: 88rpx; padding: 0 20rpx; }.textarea { height: 150rpx; }.citation { display: flex; align-items: center; gap: 6rpx; color: #61758f; font-size: 22rpx; line-height: 1.6; }.citation switch { transform: scale(.7); }.hint { display: block; margin: 10rpx 0; color: #94a0af; font-size: 21rpx; line-height: 1.6; }.save { width: 100%; margin-top: 10rpx; }
.card-head { display: flex; align-items: center; gap: 12rpx; }.title { flex: 1; color: #20344f; font-size: 30rpx; font-weight: 650; }.badge { padding: 4rpx 12rpx; border-radius: 10rpx; background: #eaf2ff; color: #3c6da8; font-size: 20rpx; }.meta, .dishes, .notes, .comments { display: block; margin-top: 10rpx; color: #8998ac; font-size: 22rpx; line-height: 1.6; }.dishes { color: #526780; }.notes { color: #61758f; }.comments { color: #6d8fb4; }
.loading, .empty { display: flex; flex-direction: column; align-items: center; gap: 12rpx; padding: 80rpx 40rpx; color: #8a98aa; font-size: 24rpx; line-height: 1.7; text-align: center; }.error { margin: 0 28rpx 16rpx; color: #b45f4e; font-size: 24rpx; line-height: 1.7; }
</style>