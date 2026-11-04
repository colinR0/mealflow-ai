<script lang="ts">
import { defineComponent } from 'vue'
import AppHeader from '../../components/AppHeader.vue'
import SurfaceCard from '../../components/SurfaceCard.vue'
import { session, login, logout, loadHouseholds } from '../../shared/session'
import { ApiError, patchRequest, request } from '../../shared/transport'

interface MemberRestriction { type: 'allergy' | 'dislike' | 'legacy_free_text'; ingredient_key?: string; ingredient_name?: string; text?: string }
interface MemberEvent { id?: string; event_type: string; start_date: string; end_date: string; constraint_tags: string[]; note: string }
interface MemberArchive {
  id: string; display_name: string; linked_user_id: string | null; gender: string; residency: 'resident' | 'guest';
  taboo: string; note: string; restrictions: Array<MemberRestriction | string>; events: MemberEvent[]; version: number
}

const EVENT_TYPES = ['感冒', '例假', '肠胃不适', '康复期', '其他']
const DIET_TAGS = ['忌辛辣', '忌生冷', '忌油腻', '忌油炸', '忌发物', '宜清淡', '宜温补', '宜软烂']

export default defineComponent({
  name: 'FamilyPage',
  components: { AppHeader, SurfaceCard },
  data() {
    return {
      session, EVENT_TYPES, DIET_TAGS, phone: '', password: '', busy: false, error: '', notice: '',
      members: [] as MemberArchive[], loadingMembers: false,
      editorOpen: false, editingId: '', editingVersion: 1, saving: false,
      form: this.emptyForm(), restrictionType: 'dislike' as 'allergy' | 'dislike',
      restrictionName: '', eventDraft: this.emptyEvent(),
    }
  },
  computed: {
    validPhone(): boolean { return /^1[3-9][0-9]{9}$/.test(this.phone) },
    today(): string { return new Date().toISOString().slice(0, 10) },
  },
  onShow() { if (session.userId && session.householdId) void this.loadMembers() },
  watch: {
    'session.householdId'() { this.members = []; if (session.userId && session.householdId) void this.loadMembers() },
    'session.userId'(value: string) { if (value && session.householdId) void this.loadMembers(); else this.members = [] },
  },
  methods: {
    emptyForm() { return { name: '', gender: '保密', residency: 'resident' as 'resident' | 'guest', taboo: '', note: '', restrictions: [] as MemberRestriction[], events: [] as MemberEvent[] } },
    emptyEvent(): MemberEvent { return { event_type: '感冒', start_date: new Date().toISOString().slice(0, 10), end_date: new Date().toISOString().slice(0, 10), constraint_tags: [], note: '' } },
    signIn() {
      if (this.busy) return
      this.busy = true; this.error = ''; this.notice = ''
      return login(this.phone, this.password).then(() => {
        this.notice = '已登录并加载可访问的家庭。'
        return this.loadMembers()
      }, (error: unknown) => { this.error = error instanceof Error ? error.message : '登录失败' })
        .finally(() => { this.password = ''; this.busy = false })
    },
    signOut() {
      this.busy = true; this.error = ''
      return logout().then(() => { this.members = []; this.notice = '已退出登录.' }, (error: unknown) => { this.error = error instanceof Error ? error.message : '退出失败' })
        .finally(() => { this.busy = false })
    },
    reload() {
      this.busy = true; this.error = ''
      return loadHouseholds().then(() => this.loadMembers(), (error: unknown) => { this.error = error instanceof Error ? error.message : '家庭列表加载失败' })
        .finally(() => { this.busy = false })
    },
    loadMembers() {
      const householdId = session.householdId
      if (!householdId) return
      this.loadingMembers = true; this.error = ''
      return request<MemberArchive[]>(`/households/${householdId}/profiles`)
        .then((members) => { if (householdId === session.householdId) this.members = members }, (error: unknown) => { this.error = error instanceof Error ? error.message : '人员信息加载失败' })
        .finally(() => { this.loadingMembers = false })
    },
    openCreate() { this.editingId = ''; this.editingVersion = 1; this.form = this.emptyForm(); this.eventDraft = this.emptyEvent(); this.restrictionType = 'dislike'; this.restrictionName = ''; this.editorOpen = true },
    openEdit(member: MemberArchive) {
      uni.navigateTo({ url: `/pages/family/profile-edit?id=${encodeURIComponent(member.id)}` })
    },
    addRestriction() {
      const name = this.restrictionName.trim()
      if (!name) return
      const key = name.toLowerCase()
      if (this.form.restrictions.some((item: MemberRestriction) => item.type === this.restrictionType && (item.ingredient_key || item.text)?.toLowerCase() === key)) { uni.showToast({ title: '这项已在档案中', icon: 'none' }); return }
      this.form.restrictions.push({ type: this.restrictionType, ingredient_key: name, ingredient_name: name })
      this.restrictionName = ''
    },
    removeRestriction(index: number) { this.form.restrictions.splice(index, 1) },
    toggleDietTag(tag: string) {
      const tags = this.eventDraft.constraint_tags
      const index = tags.indexOf(tag)
      if (index >= 0) tags.splice(index, 1)
      else tags.push(tag)
    },
    addEvent() {
      if (!this.eventDraft.event_type.trim() || !this.eventDraft.start_date || !this.eventDraft.end_date || this.eventDraft.end_date < this.eventDraft.start_date) { uni.showToast({ title: '请检查事件名称和日期范围', icon: 'none' }); return }
      this.form.events.push({ ...this.eventDraft, constraint_tags: [...this.eventDraft.constraint_tags] })
      this.eventDraft = this.emptyEvent()
    },
    removeEvent(index: number) { this.form.events.splice(index, 1) },
    eventState(event: MemberEvent) { return event.start_date > this.today ? '未开始' : event.end_date < this.today ? '已结束' : '进行中' },
    restrictionLabel(item: MemberRestriction | string) {
      if (typeof item === 'string') return item
      const value = item.ingredient_name || item.text || item.ingredient_key || ''
      return `${item.type === 'allergy' ? '过敏' : item.type === 'legacy_free_text' ? '' : '忌口'}${item.type === 'legacy_free_text' ? '' : ' · '}${value}`
    },
    saveMember() {
      if (this.saving || !session.householdId) return
      if (!this.form.name.trim()) { uni.showToast({ title: '请填写姓名', icon: 'none' }); return }
      this.saving = true; this.error = ''
      const householdId = session.householdId
      const payload = { name: this.form.name.trim(), gender: this.form.gender, residency: this.form.residency, taboo: this.form.taboo.trim(), note: this.form.note.trim(), restrictions: this.form.restrictions, events: this.form.events }
      const save = this.editingId
        ? patchRequest(`/households/${householdId}/profiles/${this.editingId}`, { ...payload, expected_version: this.editingVersion })
        : request(`/households/${householdId}/profiles`, payload, true, {}, 'POST')
      return save.then(() => {
        this.editorOpen = false
        return this.loadMembers()
      }).then(() => { this.notice = this.editingId ? '人员档案已更新。' : '人员已加入家庭档案.' }, (error: unknown) => {
        this.error = error instanceof ApiError && error.statusCode === 409 ? '档案刚刚被其他人更新，请刷新后重新编辑。' : error instanceof Error ? error.message : '保存失败'
      }).finally(() => { this.saving = false })
    },
    removeMember(member: MemberArchive) {
      uni.showModal({ title: '删除人员档案', content: `确定删除「${member.display_name}」及其忌口和特殊情况吗？`, success: async (result) => {
        if (!result.confirm) return
        this.error = ''
        request(`/households/${session.householdId}/profiles/${member.id}?expected_version=${member.version}`, undefined, true, {}, 'DELETE').then(() => {
          this.members = this.members.filter((item) => item.id !== member.id)
          this.notice = '人员档案已删除。'
        }, (error: unknown) => { this.error = error instanceof Error ? error.message : '删除失败' })
      } })
    },
    closeEditor() { this.editorOpen = false },
  },
})
</script>

<template>
  <view class="page-scroll">
    <AppHeader eyebrow="HOME, CONNECTED" title="我的家庭" description="管理家人的饮食档案，让每一餐都照顾到不同需要。" />
    <view class="profile-card"><view class="profile-avatar">家</view><view><text class="profile-title">{{ session.userId ? '欢迎回家' : '开启你的新鲜生活' }}</text><text class="profile-subtitle">{{ session.userId ? '家庭用户 · 一起照顾家的日常' : '手机号登录，连接你的智慧冰箱' }}</text></view></view>

    <SurfaceCard :title="session.userId ? '我的家庭' : '登录家庭账号'" :action="session.userId ? '刷新' : ''" @action="reload">
      <text v-if="error" class="error-copy">{{ error }}</text><text v-if="notice" class="notice-copy">{{ notice }}</text>
      <view v-if="!session.userId" class="login-form"><text class="field-label">手机号</text><input v-model="phone" class="form-input" type="number" maxlength="11" placeholder="请输入 11 位手机号" /><text v-if="phone && !validPhone" class="phone-hint">请输入有效的 11 位手机号</text><text class="field-label">密码</text><input v-model="password" class="form-input" password maxlength="256" placeholder="请输入密码" /><button class="primary-button" :disabled="busy || !validPhone || !password" @tap="signIn">{{ busy ? '正在登录…' : '登录' }}</button><text class="footnote">仅支持已开通的手机号账号，不提供邮箱登录。</text></view>
      <view v-else-if="!session.households.length" class="empty-state"><text>当前账号尚未加入家庭，可向家人获取邀请。</text><button class="secondary-button" :disabled="busy" @tap="signOut">退出登录</button></view>
      <view v-else><view v-for="household in session.households" :key="household.id" class="household-row"><text>{{ household.name }}</text><text class="household-state">{{ household.id === session.householdId ? '当前家庭' : '可在页面顶部切换' }}</text></view><button class="secondary-button" :disabled="busy" @tap="signOut">退出登录</button></view>
    </SurfaceCard>

    <SurfaceCard v-if="session.userId && session.householdId" title="人员信息" :action="loadingMembers ? '加载中…' : '新增人员'" @action="!loadingMembers && openCreate()">
      <view v-if="loadingMembers && !members.length" class="empty-state"><text>正在读取人员档案…</text></view>
      <view v-else-if="!members.length" class="empty-state"><text class="empty-title">添加家人饮食档案</text><text>记录称呼、过敏忌口和有期限的特殊情况。</text><button class="primary-button" @tap="openCreate">新增人员</button></view>
      <view v-else class="member-list">
        <view v-for="member in members" :key="member.id" class="member-row">
          <view class="member-top"><view class="member-avatar">{{ member.display_name.slice(0, 1) }}</view><view class="member-heading"><text class="member-name">{{ member.display_name }}</text><text class="member-meta">{{ member.gender }} · {{ member.residency === 'resident' ? '常驻成员' : '访客' }}</text></view><view class="member-actions"><text class="icon-action" aria-label="编辑人员档案" @tap="openEdit(member)">✎</text><text class="icon-action delete-action" aria-label="删除人员档案" @tap="removeMember(member)">×</text></view></view>
          <text v-if="member.taboo" class="member-note">{{ member.taboo }}</text>
          <view v-if="member.restrictions.length" class="tag-row"><text v-for="(item, index) in member.restrictions" :key="index" class="restriction-chip" :class="{ allergy: typeof item !== 'string' && item.type === 'allergy' }">{{ restrictionLabel(item) }}</text></view>
          <view v-if="member.events.length" class="event-list"><view v-for="event in member.events" :key="event.id || `${event.event_type}-${event.start_date}`" class="event-row"><view class="event-heading"><text class="event-name">{{ event.event_type }}</text><text class="event-state" :class="{ active: eventState(event) === '进行中' }">{{ eventState(event) }}</text></view><text class="event-dates">{{ event.start_date }} 至 {{ event.end_date }}</text><text v-if="event.constraint_tags.length" class="event-tags">{{ event.constraint_tags.join('、') }}</text><text v-if="event.note" class="event-tags">{{ event.note }}</text></view></view>
          <text v-if="!member.restrictions.length && !member.events.length && !member.taboo" class="no-details">尚未设置饮食信息</text>
        </view>
      </view>
    </SurfaceCard>
    <button v-if="session.userId && session.households.length" class="logout-link" :disabled="busy" @tap="signOut">退出登录</button>

    <view v-if="editorOpen" class="modal-backdrop" @tap.self="closeEditor">
      <scroll-view scroll-y class="editor-sheet">
        <view class="editor-heading"><text class="editor-title">{{ editingId ? '编辑人员档案' : '新增人员' }}</text><text class="close-action" aria-label="关闭" @tap="closeEditor">×</text></view>
        <text class="section-label">基础信息</text>
        <text class="field-label">姓名</text><input v-model="form.name" class="form-input" maxlength="80" placeholder="怎么称呼这位家人" />
        <text class="field-label">性别</text><picker :range="['保密', '女', '男']" @change="form.gender = ['保密', '女', '男'][Number($event.detail.value)]"><view class="form-select">{{ form.gender }} <text>⌄</text></view></picker>
        <text class="field-label">家庭身份</text><view class="segmented"><text :class="{ selected: form.residency === 'resident' }" @tap="form.residency = 'resident'">常驻成员</text><text :class="{ selected: form.residency === 'guest' }" @tap="form.residency = 'guest'">访客</text></view>
        <text class="field-label">自由描述</text><textarea v-model="form.taboo" class="form-textarea" maxlength="500" placeholder="例如：不吃香菜、海鲜过敏" />
        <text class="field-label">备注</text><textarea v-model="form.note" class="form-textarea compact-textarea" maxlength="500" placeholder="补充照护信息" />

        <view class="section-divider"><text class="section-label">过敏与忌口</text><text class="section-hint">过敏请单独标明</text></view>
        <view class="add-restriction"><picker :range="['忌口', '过敏']" @change="restrictionType = Number($event.detail.value) === 0 ? 'dislike' : 'allergy'"><view class="form-select type-select">{{ restrictionType === 'allergy' ? '过敏' : '忌口' }}⌄</view></picker><input v-model="restrictionName" class="form-input restriction-input" placeholder="输入食材或描述" confirm-type="done" @confirm="addRestriction" /><text class="add-button" @tap="addRestriction">添加</text></view>
        <view v-if="form.restrictions.length" class="tag-row editor-tags"><view v-for="(item, index) in form.restrictions" :key="index" class="editable-chip" :class="{ allergy: typeof item !== 'string' && item.type === 'allergy' }"><text>{{ restrictionLabel(item) }}</text><text class="chip-remove" @tap="removeRestriction(index)">×</text></view></view>
        <text v-else class="no-details">尚未添加</text>

        <view class="section-divider"><text class="section-label">特殊情况</text><text class="section-hint">仅在设置日期内生效</text></view>
        <view v-for="(event, index) in form.events" :key="event.id || `draft-${index}`" class="draft-event"><view class="event-heading"><text class="event-name">{{ event.event_type }}</text><text class="remove-text" @tap="removeEvent(index)">移除</text></view><text class="event-dates">{{ event.start_date }} 至 {{ event.end_date }} · {{ eventState(event) }}</text><text v-if="event.constraint_tags.length" class="event-tags">{{ event.constraint_tags.join('、') }}</text></view>
        <text class="field-label">情况类型</text><picker :range="EVENT_TYPES" @change="eventDraft.event_type = EVENT_TYPES[Number($event.detail.value)]"><view class="form-select">{{ eventDraft.event_type }}<text>⌄</text></view></picker>
        <text class="field-label">其他情况名称</text><input v-model="eventDraft.event_type" class="form-input" maxlength="32" placeholder="可填写自定义名称" />
        <text class="field-label">开始日期</text><picker mode="date" :value="eventDraft.start_date" @change="eventDraft.start_date = $event.detail.value"><view class="form-select">{{ eventDraft.start_date }}<text>选择</text></view></picker>
        <text class="field-label">结束日期</text><picker mode="date" :value="eventDraft.end_date" :start="eventDraft.start_date" @change="eventDraft.end_date = $event.detail.value"><view class="form-select">{{ eventDraft.end_date }}<text>选择</text></view></picker>
        <text class="field-label">饮食要求 · 可多选</text><view class="tag-row selectable-tags"><text v-for="tag in DIET_TAGS" :key="tag" class="selectable-chip" :class="{ chosen: eventDraft.constraint_tags.includes(tag) }" @tap="toggleDietTag(tag)">{{ tag }}</text></view>
        <text class="field-label">补充说明</text><textarea v-model="eventDraft.note" class="form-textarea compact-textarea" maxlength="200" placeholder="按实际情况填写" />
        <button class="secondary-button add-event-button" @tap="addEvent">加入特殊情况</button>
        <text class="privacy-note">饮食要求由家庭自行设置；特殊情况到期后不再作为当前约束。</text>
        <view class="editor-footer"><button class="cancel-button" @tap="closeEditor">取消</button><button class="primary-button save-button" :disabled="saving" @tap="saveMember">{{ saving ? '保存中…' : '保存档案' }}</button></view>
      </scroll-view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.page-scroll { min-height: 100vh; }
.profile-card { display: flex; align-items: center; gap: 24rpx; padding: 20rpx 40rpx 42rpx; }
.profile-card > view:last-child { min-width: 0; }
.profile-avatar, .member-avatar { display: flex; align-items: center; justify-content: center; flex-shrink: 0; background: linear-gradient(140deg,#7baefa,#3478f6); color: #fff; }
.profile-avatar { width: 112rpx; height: 112rpx; border: 6rpx solid #fff; border-radius: 40rpx; font-size: 44rpx; box-shadow: 0 12rpx 30rpx #3478f626; }
.profile-title, .profile-subtitle { display: block; }
.profile-title { color: #29415f; font-size: 34rpx; font-weight: 650; }
.profile-subtitle { margin-top: 12rpx; color: #7a8fa9; font-size: 24rpx; line-height: 1.6; }
.login-form { display: flex; flex-direction: column; gap: 12rpx; }
.field-label { display: block; margin: 18rpx 0 10rpx; color: #607896; font-size: 25rpx; }
.form-input, .form-textarea { width: 100%; min-height: 92rpx; padding: 22rpx 24rpx; border: 0; border-radius: 22rpx; background: #f3f7fc; color: #29415f; font-size: 27rpx; line-height: 1.6; }
.form-textarea { min-height: 130rpx; }
.compact-textarea { min-height: 100rpx; }
.form-select { display: flex; align-items: center; justify-content: space-between; min-height: 90rpx; padding: 0 24rpx; border-radius: 22rpx; background: #f3f7fc; color: #29415f; font-size: 27rpx; }
.primary-button, .secondary-button { margin: 18rpx 0 0; border-radius: 26rpx; font-size: 26rpx; line-height: 88rpx; }
.primary-button { background: #3478f6; color: white; }
.secondary-button { background: #edf4ff; color: #3478f6; }
.primary-button[disabled], .secondary-button[disabled] { opacity: .55; }
.footnote { display: block; margin-top: 18rpx; color: #8391a7; font-size: 23rpx; line-height: 1.7; }
.phone-hint, .error-copy { display: block; color: #c34c56; font-size: 24rpx; line-height: 1.6; }
.error-copy { margin-bottom: 16rpx; }
.notice-copy { display: block; margin-bottom: 14rpx; color: #3478f6; font-size: 24rpx; line-height: 1.6; }
.household-row { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; min-height: 90rpx; border-bottom: 1rpx solid #edf2f9; color: #29415f; font-size: 27rpx; }
.household-state, .member-meta { color: #7a8fa9; font-size: 23rpx; }
.empty-state { min-height: 200rpx; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 10rpx; color: #7b8ba2; text-align: center; font-size: 25rpx; line-height: 1.7; }
.empty-title { color: #314b6d; font-size: 29rpx; font-weight: 600; }
.member-list { display: flex; flex-direction: column; }
.member-row { padding: 18rpx 0 26rpx; border-bottom: 1rpx solid #edf2f9; }
.member-row:last-child { padding-bottom: 2rpx; border-bottom: 0; }
.member-top, .event-heading { display: flex; align-items: center; gap: 18rpx; }
.member-avatar { width: 84rpx; height: 84rpx; border: 4rpx solid #fff; border-radius: 28rpx; font-size: 36rpx; }
.member-heading { flex: 1; min-width: 0; }
.member-name, .member-meta { display: block; overflow-wrap: anywhere; }
.member-name { color: #29415f; font-size: 29rpx; font-weight: 650; }
.member-meta { margin-top: 6rpx; }
.member-actions { display: flex; gap: 8rpx; flex-shrink: 0; }
.icon-action { display: flex; align-items: center; justify-content: center; width: 64rpx; height: 64rpx; border-radius: 50%; background: #edf4ff; color: #3478f6; font-size: 36rpx; }
.delete-action, .remove-text { color: #bd6467; }
.member-note, .event-tags, .event-dates, .no-details { display: block; margin-top: 14rpx; color: #72849e; font-size: 23rpx; line-height: 1.65; overflow-wrap: anywhere; }
.tag-row { display: flex; flex-wrap: wrap; gap: 10rpx; margin-top: 14rpx; }
.restriction-chip, .selectable-chip, .editable-chip { display: inline-flex; align-items: center; max-width: 100%; min-height: 52rpx; padding: 8rpx 16rpx; border-radius: 24rpx; background: #fff3e9; color: #a5653e; font-size: 21rpx; line-height: 1.4; overflow-wrap: anywhere; }
.restriction-chip.allergy, .editable-chip.allergy { background: #fff0ef; color: #b54e52; }
.event-list { margin-top: 18rpx; padding-left: 18rpx; border-left: 3rpx solid #dceaff; }
.event-row { padding: 10rpx 0 12rpx 14rpx; }
.event-name { color: #314b6d; font-size: 25rpx; font-weight: 600; }
.event-state { margin-left: auto; padding: 6rpx 14rpx; border-radius: 22rpx; background: #eef2f7; color: #72849e; font-size: 20rpx; }
.event-state.active { background: #fff4df; color: #996d23; }
.event-dates { margin-top: 6rpx; font-variant-numeric: tabular-nums; }
.logout-link { display: block; margin: 2rpx auto 40rpx; padding: 18rpx 34rpx; background: transparent; color: #7587a0; font-size: 24rpx; }
.modal-backdrop { position: fixed; z-index: 100; inset: 0; display: flex; align-items: flex-end; background: rgba(21,39,62,.44); }
.editor-sheet { width: 100%; max-height: 92vh; padding: 28rpx 36rpx calc(28rpx + env(safe-area-inset-bottom)); border-radius: 32rpx 32rpx 0 0; background: #fff; box-sizing: border-box; }
.editor-heading, .section-divider { display: flex; align-items: center; justify-content: space-between; gap: 14rpx; }
.editor-heading { padding-bottom: 14rpx; border-bottom: 1rpx solid #edf2f9; }
.editor-title { color: #29415f; font-size: 32rpx; font-weight: 700; }
.close-action { display: flex; align-items: center; justify-content: center; width: 70rpx; height: 70rpx; color: #71839d; font-size: 44rpx; }
.section-divider { margin-top: 30rpx; }
.section-label { display: block; margin-top: 24rpx; color: #29415f; font-size: 27rpx; font-weight: 650; }
.section-hint { margin-top: 24rpx; color: #8391a7; font-size: 21rpx; }
.segmented { display: flex; padding: 6rpx; border-radius: 22rpx; background: #f0f4fa; }
.segmented text { flex: 1; padding: 18rpx 8rpx; border-radius: 18rpx; color: #72849e; text-align: center; font-size: 24rpx; }
.segmented text.selected { background: #fff; color: #3478f6; box-shadow: 0 4rpx 12rpx #304c7012; }
.add-restriction { display: flex; align-items: center; gap: 10rpx; }
.type-select { min-width: 120rpx; min-height: 82rpx; padding: 0 16rpx; font-size: 23rpx; }
.restriction-input { flex: 1; min-width: 0; min-height: 82rpx; padding: 16rpx; font-size: 23rpx; }
.add-button { padding: 18rpx 12rpx; color: #3478f6; font-size: 23rpx; }
.editor-tags { margin-top: 12rpx; }
.editable-chip { gap: 10rpx; }
.chip-remove { color: #8c93a0; font-size: 28rpx; }
.draft-event { margin-top: 14rpx; padding: 18rpx; border-radius: 20rpx; background: #f6f9fe; }
.remove-text { margin-left: auto; padding: 12rpx; font-size: 22rpx; }
.selectable-tags { gap: 12rpx; }
.selectable-chip { min-height: 60rpx; background: #f0f4fa; color: #72849e; }
.selectable-chip.chosen { background: #e5efff; color: #3478f6; }
.add-event-button { width: 100%; }
.privacy-note { display: block; margin-top: 16rpx; color: #8391a7; font-size: 21rpx; line-height: 1.6; }
.editor-footer { display: flex; gap: 16rpx; margin-top: 24rpx; }
.cancel-button, .save-button { flex: 1; margin: 0; border-radius: 22rpx; font-size: 25rpx; line-height: 84rpx; }
.cancel-button { background: #f0f4fa; color: #607896; }
</style>
