<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'
import { session } from '../../shared/session'
import { patchRequest, request } from '../../shared/transport'
import type { Household } from '../../shared/types'

interface Restriction { type: 'allergy' | 'dislike'; ingredient_key: string; ingredient_name: string }
interface SpecialEvent { event_type: string; start_date: string; end_date: string; constraint_tags: string[]; note: string }
interface Profile { id: string; display_name: string; linked_user_id: string | null; gender: string; residency: 'resident' | 'guest'; taboo: string; note: string; restrictions: Array<Restriction | string>; events: SpecialEvent[]; version: number }
const EVENT_TYPES = ['肠胃不适', '感冒', '例假', '康复期', '其他']
const DIET_TAGS = ['清淡', '温热', '忌生冷', '少渣', '少油', '少辣']

export default defineComponent({
  name: 'FamilyProfileEditPage',
  components: { SubpageHeader, BottomNav },
  data() {
    return { session, member: null as Profile | null, loading: true, saving: false, error: '', name: '', gender: '保密', residency: 'resident' as 'resident' | 'guest', taboo: '', note: '', restrictions: [] as Restriction[], events: [] as SpecialEvent[], restrictionType: 'dislike' as 'allergy' | 'dislike', restrictionName: '', eventType: '肠胃不适', startDate: new Date().toISOString().slice(0, 10), endDate: new Date().toISOString().slice(0, 10), eventTags: [] as string[], eventNote: '', EVENTS: EVENT_TYPES, TAGS: DIET_TAGS }
  },
  onLoad(options: { id?: string }) {
    const householdId = session.householdId
    if (!householdId || !options.id) { this.loading = false; this.error = '缺少家庭或人员档案信息。'; return }
    void request<Profile[]>(`/households/${householdId}/profiles`).then((profiles) => {
      const member = profiles.find((profile) => profile.id === options.id)
      if (!member) { this.error = '人员档案已不存在。'; this.loading = false; return }
      this.member = member
      this.name = member.display_name
      this.gender = member.gender || '保密'
      this.residency = member.residency || 'resident'
      this.taboo = member.taboo || ''
      this.note = member.note || ''
      this.restrictions = member.restrictions.filter((item): item is Restriction => typeof item !== 'string').map((item) => ({ ...item }))
      this.events = member.events.map((item) => ({ ...item, constraint_tags: [...item.constraint_tags] }))
      this.loading = false
    }, (error: unknown) => { this.error = error instanceof Error ? error.message : '人员档案加载失败'; this.loading = false })
  },
  computed: {
    dateInvalid(): boolean { return this.endDate < this.startDate },
    eventState(): string { const today = new Date().toISOString().slice(0, 10); return this.endDate < today ? '已结束' : this.startDate > today ? '未开始' : '进行中' },
  },
  methods: {
    toggleTag(tag: string) { const index = this.eventTags.indexOf(tag); if (index >= 0) this.eventTags.splice(index, 1); else this.eventTags.push(tag) },
    addRestriction() { const name = this.restrictionName.trim(); if (!name) return; this.restrictions.push({ type: this.restrictionType, ingredient_key: name, ingredient_name: name }); this.restrictionName = '' },
    removeRestriction(index: number) { this.restrictions.splice(index, 1) },
    addEvent() {
      if (this.dateInvalid || !this.eventType.trim()) return
      this.events.push({ event_type: this.eventType, start_date: this.startDate, end_date: this.endDate, constraint_tags: [...this.eventTags], note: this.eventNote.trim() })
      this.eventTags = []; this.eventNote = ''
    },
    save() {
      if (!this.member || this.saving || !this.name.trim() || this.dateInvalid) return
      this.saving = true
      const payload = { name: this.name.trim(), gender: this.gender, residency: this.residency, taboo: this.taboo.trim(), note: this.note.trim(), restrictions: this.restrictions, events: this.events, expected_version: this.member.version }
      void patchRequest(`/households/${session.householdId}/profiles/${this.member.id}`, payload).then(() => {
        uni.showToast({ title: '档案已保存', icon: 'success' })
        uni.navigateBack({ delta: 1 })
      }, (error: unknown) => { this.error = error instanceof Error ? error.message : '保存失败' }).finally(() => { this.saving = false })
    },
    cancel() { uni.navigateBack({ delta: 1 }) },
  },
})
</script>

<template>
  <view class="secondary-page">
    <SubpageHeader title="编辑人员档案" />
    <scroll-view scroll-y class="secondary-scroll">
      <view v-if="loading" class="state-copy">正在读取人员档案…</view><view v-else-if="error && !member" class="state-copy error-copy">{{ error }}</view>
      <template v-else-if="member">
        <view class="form-panel"><text class="panel-title">基础信息</text><view class="form-row"><text>姓名</text><input v-model="name" maxlength="80" placeholder="必填" /></view><picker :range="['常驻成员', '访客']" @change="residency = Number($event.detail.value) ? 'guest' : 'resident'"><view class="form-row"><text>身份</text><text>{{ residency === 'resident' ? '常驻成员' : '访客' }}　⌄</text></view></picker><picker :range="['保密', '女', '男']" @change="gender = ['保密', '女', '男'][Number($event.detail.value)]"><view class="form-row"><text>性别</text><text>{{ gender }}　⌄</text></view></picker><textarea v-model="note" maxlength="500" placeholder="备注，例如平时晚餐在家吃" /></view>
        <view class="form-panel"><text class="panel-title">过敏与忌口</text><view class="form-row"><text>限制类型</text><picker :range="['忌口', '过敏']" @change="restrictionType = Number($event.detail.value) ? 'allergy' : 'dislike'"><view>{{ restrictionType === 'allergy' ? '过敏' : '忌口' }}　⌄</view></picker></view><view class="add-row"><input v-model="restrictionName" placeholder="输入食材或描述" /><text @tap="addRestriction">添加</text></view><view v-for="(item, index) in restrictions" :key="index" class="restriction-row"><text :class="{ allergy: item.type === 'allergy' }">{{ item.type === 'allergy' ? '⚠ 过敏：' : '忌口：' }}{{ item.ingredient_name }}</text><text class="remove" @tap="removeRestriction(index)">移除</text></view><text v-if="!restrictions.length" class="muted">暂未添加结构化限制</text></view>
        <view class="form-panel"><text class="panel-title">临时特殊情况</text><view v-for="(event, index) in events" :key="index" class="event-row"><view><text class="event-name">{{ event.event_type }}</text><text class="muted">{{ event.start_date }} 至 {{ event.end_date }} · {{ event.end_date < new Date().toISOString().slice(0, 10) ? '已结束' : event.start_date > new Date().toISOString().slice(0, 10) ? '未开始' : '进行中' }}</text><text v-if="event.constraint_tags.length" class="muted">{{ event.constraint_tags.join('、') }}</text></view><text class="remove" @tap="events.splice(index, 1)">移除</text></view><picker :range="EVENTS" @change="eventType = EVENTS[Number($event.detail.value)]"><view class="form-row"><text>情况类型</text><text>{{ eventType }}　⌄</text></view></picker><view class="form-row"><text>开始日期</text><picker mode="date" :value="startDate" @change="startDate = $event.detail.value">{{ startDate }}　▦</picker></view><view class="form-row"><text>结束日期</text><picker mode="date" :value="endDate" :start="startDate" @change="endDate = $event.detail.value">{{ endDate }}　▦</picker></view><text v-if="dateInvalid" class="date-error">结束日期不得早于开始日期</text><text class="event-state" :class="eventState === '进行中' ? 'active' : ''">{{ eventState }}</text><view class="tag-list"><text v-for="tag in TAGS" :key="tag" :class="['tag', { chosen: eventTags.includes(tag) }]" @tap="toggleTag(tag)">{{ tag }}</text></view><textarea v-model="eventNote" maxlength="200" placeholder="补充说明（可选）" /><button class="secondary-button" :disabled="dateInvalid" @tap="addEvent">加入特殊情况</button><text class="muted disclaimer">情况仅作家庭记录，饮食要求由家庭自行选择，不提供医疗建议。</text></view>
        <text v-if="error" class="error-copy">{{ error }}</text><view class="save-row"><button class="cancel-button" @tap="cancel">取消</button><button class="save-button" :disabled="saving || !name.trim() || dateInvalid" @tap="save">{{ saving ? '保存中…' : '保存' }}</button></view>
      </template>
    </scroll-view>
    <BottomNav active="family" />
  </view>
</template>

<style scoped lang="scss">
.secondary-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }.secondary-scroll { flex: 1; min-height: 0; padding: 16px; }.form-panel { margin-bottom: 14px; padding: 0 16px 16px; border-radius: 13px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }.panel-title { display: block; padding: 16px 0 12px; border-bottom: 1px solid #e5e7eb; color: #18181b; font-size: 17px; font-weight: 600; }.form-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 52px; border-bottom: 1px solid #e5e7eb; color: #27272a; font-size: 15px; }.form-row input { min-width: 0; flex: 1; text-align: right; }.form-panel textarea { width: 100%; min-height: 76px; margin-top: 12px; padding: 12px; border-radius: 8px; background: #f3f4f6; font-size: 14px; }.add-row { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid #e5e7eb; }.add-row input { flex: 1; min-width: 0; height: 42px; padding: 0 10px; border-radius: 8px; background: #f3f4f6; }.add-row text { color: #1d4ed8; }.restriction-row, .event-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 0; border-bottom: 1px solid #e5e7eb; color: #374151; font-size: 14px; }.allergy, .date-error { color: #b91c1c; }.remove { flex-shrink: 0; color: #71717a; }.muted { display: block; margin-top: 8px; color: #71717a; font-size: 13px; line-height: 1.5; }.event-name { display: block; color: #18181b; font-size: 15px; }.date-error { display: block; padding: 8px 0; font-size: 13px; }.event-state { display: inline-block; margin-top: 12px; padding: 5px 9px; border-radius: 7px; background: #e5e7eb; color: #52525b; font-size: 12px; }.event-state.active { background: #dcfce7; color: #166534; }.tag-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }.tag { padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 18px; color: #52525b; font-size: 13px; }.tag.chosen { border-color: #2563eb; background: #eff6ff; color: #1d4ed8; }.secondary-button { width: 100%; margin-top: 12px; }.disclaimer { margin-top: 12px; }.error-copy { color: #b91c1c; font-size: 14px; }.save-row { display: flex; gap: 10px; margin: 6px 0 20px; }.cancel-button, .save-button { flex: 1; margin: 0; border-radius: 9px; line-height: 46px; }.cancel-button { background: #e5e7eb; color: #27272a; }.save-button { background: #2563eb; color: #fff; }.save-button[disabled], .secondary-button[disabled] { opacity: .5; }.cancel-button::after, .save-button::after { border: 0; }.state-copy { padding: 36px 16px; color: #71717a; }
</style>
