<script lang="ts">
import { defineComponent, markRaw } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Plus, Search } from '@element-plus/icons-vue'
import {
  createMember as createMemberApi,
  createMemberEvent,
  createRestriction,
  deleteMember,
  deleteMemberEvent,
  deleteFoodPreference,
  deleteRestriction,
  updateMember,
  type MemberPayload,
  type RestrictionType,
} from '../../api/members'
import {
  fetchHousehold,
  fetchPantryConfig,
  updatePantryConfig,
  type Household,
  type HouseholdEvent,
  type HouseholdMember,
  type HouseholdRestriction,
  type PantryConfig,
} from '../../api/chat'
import { fetchIngredients, type IngredientOption } from '../../api/inventory'
const GENDERS = ['男', '女', '保密']
const EVENT_PRESETS: Array<{ type: string; tags: string[]; hint: string }> = [
  { type: '感冒', tags: ['忌辛辣', '忌油腻', '宜清淡'], hint: '清淡少油,忌辛辣油腻' },
  { type: '例假', tags: ['忌辛辣', '忌生冷'], hint: '忌辛辣生冷,宜温补' },
  { type: '肠胃不适', tags: ['忌油腻', '忌生冷', '宜清淡'], hint: '肠胃敏感,忌油腻生冷' },
  { type: '康复期', tags: ['宜清淡', '宜温补'], hint: '恢复期,清淡温补为主' },
  { type: '最近在健身', tags: ['宜高蛋白', '忌油炸', '宜清淡'], hint: '优先高蛋白,少油烹饪' },
  { type: '减脂', tags: ['忌油腻', '忌油炸', '宜清淡', '宜低卡'], hint: '控制油脂与热量' },
  { type: '上火', tags: ['忌辛辣', '忌油炸'], hint: '忌辛辣油炸' },
  { type: '熬夜体虚', tags: ['宜温补', '忌生冷'], hint: '体虚,忌生冷宜温补' },
  { type: '脾胃虚弱', tags: ['忌生冷', '忌油腻', '宜清淡'], hint: '脾胃弱,忌生冷油腻' },
  { type: '过敏期', tags: ['忌发物'], hint: '忌发物' },
  { type: '其他', tags: [], hint: '自定义约束' },
]
const TAG_OPTIONS = [
  '忌辛辣',
  '忌生冷',
  '忌油腻',
  '忌油炸',
  '忌发物',
  '忌甜',
  '宜清淡',
  '宜温补',
  '宜软烂',
]

export default defineComponent({
  name: 'MembersView',
  components: { Delete, Edit, Plus, Search },
  data() {
    return {
      GENDERS,
      EVENT_PRESETS,
      TAG_OPTIONS,
      household: null as Household | null,
      ingredients: [] as IngredientOption[],
      loading: true,
      activeFilter: 'all' as 'all' | 'resident' | 'guest',
      searchText: '',
      pantryConfig: null as PantryConfig | null,
      pantryDraft: '',
      dialogVisible: false,
      editingId: null as string | null,
      submitting: false,
      editTab: 'profile',
      form: {
        display_name: '',
        gender: '保密',
        residency: 'resident' as 'resident' | 'guest',
        taboo: '',
        note: '',
      },
      restrictionDraft: { type: 'allergy' as RestrictionType, ingredient_key: '' },
      eventDraft: {
        event_type: '感冒',
        constraint_tags: [] as string[],
        range: [] as string[],
        note: '',
      },
      pendingRestrictions: [] as Array<{
        type: RestrictionType
        ingredient_key: string
        name: string
      }>,
      pendingEvents: [] as Array<{
        event_type: string
        constraint_tags: string[]
        start: string
        end: string
        note: string
      }>,
      existingRestrictions: [] as HouseholdRestriction[],
      existingEvents: [] as HouseholdEvent[],
      originalRestrictionIds: [] as string[],
      originalEventIds: [] as string[],
      Delete: markRaw(Delete),
      Edit: markRaw(Edit),
      Plus: markRaw(Plus),
      Search: markRaw(Search),
    }
  },
  computed: {
    groupedIngredients() {
      const groups = new Map<string, IngredientOption[]>()
      for (const ingredient of this.ingredients) {
        const list = groups.get(ingredient.category) ?? []
        list.push(ingredient)
        groups.set(ingredient.category, list)
      }
      return [...groups.entries()]
    },
    activeEvents() {
      return (this.household?.events ?? []).filter((event) => event.end_date >= this.today())
    },
    stats() {
      const members = this.household?.members ?? []
      return {
        total: members.length,
        residents: members.filter((member) => member.residency === 'resident').length,
        guests: members.filter((member) => member.residency === 'guest').length,
        allergies: members.reduce(
          (sum, member) => sum + member.restrictions.filter((r) => r.type === 'allergy').length,
          0,
        ),
        events: this.activeEvents.length,
      }
    },
    filteredMembers() {
      const keyword = this.searchText.trim().toLowerCase()
      return (this.household?.members ?? [])
        .filter((member) => {
          if (this.activeFilter === 'resident') return member.residency === 'resident'
          if (this.activeFilter === 'guest') return member.residency === 'guest'
          return true
        })
        .filter((member) =>
          !keyword
            ? true
            : `${member.display_name}${member.taboo ?? ''}${member.note ?? ''}`
                .toLowerCase()
                .includes(keyword),
        )
    },
  },
  methods: {
    today() {
      return new Date().toISOString().slice(0, 10)
    },
    ingredientName(key: string) {
      return this.ingredients.find((item) => item.key === key)?.name ?? key
    },
    memberEvents(userId: string) {
      return (this.household?.events ?? [])
        .filter((event) => event.user_id === userId)
        .sort((a, b) => a.start_date.localeCompare(b.start_date))
    },
    eventState(event: { start_date: string; end_date: string }) {
      if (event.start_date > this.today()) return 'future'
      if (event.end_date < this.today()) return 'expired'
      return 'active'
    },
    restrictionLabel(type: string) {
      return type === 'allergy' ? '过敏' : '忌口'
    },
    resetDrafts() {
      this.restrictionDraft.type = 'allergy'
      this.restrictionDraft.ingredient_key = ''
      this.eventDraft.event_type = '感冒'
      this.eventDraft.constraint_tags = [...EVENT_PRESETS[0].tags]
      this.eventDraft.range = [this.today(), this.today()]
      this.eventDraft.note = ''
      this.pendingRestrictions = []
      this.pendingEvents = []
      this.existingRestrictions = []
      this.existingEvents = []
      this.originalRestrictionIds = []
      this.originalEventIds = []
      this.editTab = 'profile'
    },
    openCreate() {
      this.editingId = null
      Object.assign(this.form, {
        display_name: '',
        gender: '保密',
        residency: 'resident',
        taboo: '',
        note: '',
      })
      this.resetDrafts()
      this.dialogVisible = true
    },
    openEdit(member: HouseholdMember) {
      this.resetDrafts()
      this.editingId = member.user_id
      Object.assign(this.form, {
        display_name: member.display_name,
        gender: member.gender ?? '保密',
        residency: member.residency as 'resident' | 'guest',
        taboo: member.taboo ?? '',
        note: member.note ?? '',
      })
      this.existingRestrictions = member.restrictions.map((item) => ({ ...item }))
      this.existingEvents = this.memberEvents(member.user_id).map((item) => ({
        ...item,
        constraint_tags: [...item.constraint_tags],
      }))
      this.originalRestrictionIds = this.existingRestrictions.map((item) => item.id)
      this.originalEventIds = this.existingEvents.map((item) => item.id)
      this.dialogVisible = true
    },
    onEventTypeChange(type: string) {
      const preset = EVENT_PRESETS.find((item) => item.type === type)
      this.eventDraft.constraint_tags = preset ? [...preset.tags] : []
    },
    async stageRestriction() {
      if (!this.restrictionDraft.ingredient_key) {
        ElMessage.warning('请先选择食材')
        return
      }
      const exists =
        this.existingRestrictions.some(
          (item) =>
            item.type === this.restrictionDraft.type &&
            item.ingredient_key === this.restrictionDraft.ingredient_key,
        ) ||
        this.pendingRestrictions.some(
          (item) =>
            item.type === this.restrictionDraft.type &&
            item.ingredient_key === this.restrictionDraft.ingredient_key,
        )
      if (exists) {
        ElMessage.info('这条限制已经在档案里了')
        return
      }
      const payload = {
        type: this.restrictionDraft.type,
        ingredient_key: this.restrictionDraft.ingredient_key,
        name: this.ingredientName(this.restrictionDraft.ingredient_key),
      }
      this.pendingRestrictions.push(payload)
      ElMessage.success(`已暂存${this.restrictionLabel(payload.type)}：${payload.name}，保存后生效`)
      this.restrictionDraft.ingredient_key = ''
    },
    removeRestriction(restrictionId: string) {
      this.existingRestrictions = this.existingRestrictions.filter(
        (item) => item.id !== restrictionId,
      )
    },
    async removeFoodPreference(preferenceId: string) {
      try {
        this.household = await deleteFoodPreference(preferenceId)
        ElMessage.success('长期菜品偏好已删除')
      } catch {
        ElMessage.error('删除长期菜品偏好失败')
      }
    },
    async stageEvent() {
      if (!this.eventDraft.range || this.eventDraft.range.length < 2) {
        ElMessage.warning('请选择生效日期')
        return
      }
      const payload = {
        event_type: this.eventDraft.event_type,
        constraint_tags: [...this.eventDraft.constraint_tags],
        start: this.eventDraft.range[0],
        end: this.eventDraft.range[1],
        note: this.eventDraft.note,
      }
      this.pendingEvents.push(payload)
      ElMessage.success(`已暂存「${payload.event_type}」，保存后生效`)
    },
    removeEvent(eventId: string) {
      this.existingEvents = this.existingEvents.filter((item) => item.id !== eventId)
    },
    removePendingRestriction(index: number) {
      this.pendingRestrictions.splice(index, 1)
    },
    removePendingEvent(index: number) {
      this.pendingEvents.splice(index, 1)
    },
    async submit() {
      if (!this.form.display_name.trim()) {
        ElMessage.warning('请填写名字')
        return
      }
      this.submitting = true
      const payload: MemberPayload = {
        display_name: this.form.display_name.trim(),
        gender: this.form.gender,
        residency: this.form.residency,
        taboo: this.form.taboo || null,
        note: this.form.note || null,
      }
      try {
        let targetUserId = this.editingId
        if (this.editingId) {
          this.household = await updateMember(this.editingId, payload)
        } else {
          this.household = await createMemberApi(payload)
          const created = this.household.members.find(
            (member) => member.display_name === payload.display_name,
          )
          targetUserId = created?.user_id ?? null
        }
        if (targetUserId) {
          const retainedRestrictionIds = new Set(this.existingRestrictions.map((item) => item.id))
          for (const id of this.originalRestrictionIds) {
            if (!retainedRestrictionIds.has(id)) this.household = await deleteRestriction(id)
          }
          for (const item of this.pendingRestrictions) {
            this.household = await createRestriction({
              user_id: targetUserId,
              type: item.type,
              ingredient_key: item.ingredient_key,
            })
          }
          const retainedEventIds = new Set(this.existingEvents.map((item) => item.id))
          for (const id of this.originalEventIds) {
            if (!retainedEventIds.has(id)) this.household = await deleteMemberEvent(id)
          }
          for (const item of this.pendingEvents) {
            this.household = await createMemberEvent({
              user_id: targetUserId,
              event_type: item.event_type,
              constraint_tags: item.constraint_tags,
              start_date: item.start,
              end_date: item.end,
              note: item.note || null,
            })
          }
        }
        ElMessage.success(this.editingId ? '成员档案已更新' : '成员已加入家庭档案')
        this.dialogVisible = false
      } finally {
        this.submitting = false
      }
    },
    async remove(member: HouseholdMember) {
      try {
        await ElMessageBox.confirm(
          `确定删除「${member.display_name}」的档案吗？其忌口、过敏与特殊事件会一并删除。`,
          '删除成员',
          { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
        )
      } catch {
        return
      }
      this.household = await deleteMember(member.user_id)
      ElMessage.success('已删除')
    },
    async load() {
      this.loading = true
      try {
        const [home, options, pantry] = await Promise.all([
          fetchHousehold(),
          fetchIngredients(),
          fetchPantryConfig(),
        ])
        this.household = home
        this.ingredients = options
        this.pantryConfig = pantry
      } finally {
        this.loading = false
      }
    },
    async savePantry() {
      if (!this.pantryConfig) return
      const custom = this.pantryDraft
        .split(/[,，\s]+/)
        .map((item) => item.trim())
        .filter(Boolean)
      this.pantryConfig = await updatePantryConfig({
        ...this.pantryConfig,
        custom_keys: [...new Set([...this.pantryConfig.custom_keys, ...custom])],
      })
      this.pantryDraft = ''
      ElMessage.success('常备料设置已保存')
    },
    async removeCustomPantry(key: string) {
      if (!this.pantryConfig) return
      this.pantryConfig = await updatePantryConfig({
        ...this.pantryConfig,
        custom_keys: this.pantryConfig.custom_keys.filter((item) => item !== key),
      })
      ElMessage.success(`已删除自定义常备料：${key}`)
    },
  },
  mounted() {
    return this.load()
  },
})
</script>

<template>
  <section class="members-page">
    <header class="page-hero">
      <div>
        <span class="eyebrow">FAMILY ARCHIVE</span>
        <h2>人员信息</h2>
        <p>家庭饮食档案库:三层记忆分清红线、长期偏好与临时事件,自动纳入每次 Agent 规划。</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新增人员</el-button>
    </header>

    <div class="summary-strip">
      <div>
        <span>总人数</span><strong>{{ stats.total }}</strong
        ><small>位</small>
      </div>
      <div>
        <span>常驻成员</span><strong>{{ stats.residents }}</strong
        ><small>位</small>
      </div>
      <div>
        <span>访客</span><strong>{{ stats.guests }}</strong
        ><small>位</small>
      </div>
      <div>
        <span>生效中事件</span><strong>{{ stats.events }}</strong
        ><small>个</small>
      </div>
    </div>

    <div class="legend-row">
      <span class="legend"><i class="dot red"></i>红线 · 过敏原(强制过滤)</span>
      <span class="legend"><i class="dot orange"></i>长期 · 忌口偏好</span>
      <span class="legend"><i class="dot yellow"></i>临时 · 特殊事件(带生效期)</span>
    </div>

    <section v-if="pantryConfig" class="pantry-card">
      <div>
        <strong>厨房常备料</strong>
        <p>常备料不需要录入冰箱，也不会自动加入采购清单；仍会参与过敏与忌口校验。</p>
      </div>
      <div class="pantry-controls">
        <el-switch
          v-model="pantryConfig.use_defaults"
          active-text="启用默认库"
          @change="savePantry"
        />
        <el-input
          v-model="pantryDraft"
          size="small"
          placeholder="新增自定义项，如：黄油、孜然"
          @keyup.enter="savePantry"
        />
        <el-button size="small" @click="savePantry">保存</el-button>
      </div>
      <div class="pantry-tags">
        <el-tag
          v-for="item in pantryConfig.effective_items"
          :key="item.key"
          size="small"
          effect="plain"
          :closable="item.is_custom"
          @close="item.is_custom && removeCustomPantry(item.key)"
        >
          {{ item.label }}<small v-if="item.is_custom"> · 自定义</small>
        </el-tag>
      </div>
    </section>

    <div class="filter-bar">
      <el-input
        v-model="searchText"
        :prefix-icon="Search"
        clearable
        placeholder="搜索姓名、忌口或备注"
      />
      <div class="tag-filter">
        <button :class="{ active: activeFilter === 'all' }" @click="activeFilter = 'all'">
          全部
        </button>
        <button :class="{ active: activeFilter === 'resident' }" @click="activeFilter = 'resident'">
          常驻
        </button>
        <button :class="{ active: activeFilter === 'guest' }" @click="activeFilter = 'guest'">
          访客
        </button>
      </div>
    </div>

    <div v-loading="loading" class="card-grid">
      <article v-for="member in filteredMembers" :key="member.user_id" class="member-card">
        <div class="card-top">
          <div class="avatar" :class="member.residency">{{ member.display_name.slice(0, 1) }}</div>
          <div class="who">
            <strong>{{ member.display_name }}</strong>
            <div class="who-tags">
              <el-tag v-if="member.gender" size="small" effect="plain">{{ member.gender }}</el-tag>
              <el-tag
                :type="member.residency === 'resident' ? 'success' : 'warning'"
                effect="dark"
                size="small"
              >
                {{ member.residency === 'resident' ? '常驻' : '访客' }}
              </el-tag>
            </div>
          </div>
          <div class="card-actions">
            <el-button :icon="Edit" size="small" circle text @click="openEdit(member)" />
            <el-button
              :icon="Delete"
              size="small"
              circle
              text
              type="danger"
              @click="remove(member)"
            />
          </div>
        </div>

        <div class="card-body">
          <div v-if="member.taboo" class="info-line">
            <small>忌口</small>
            <span class="taboo-text">{{ member.taboo }}</span>
          </div>
          <div v-if="member.note" class="info-line">
            <small>备注</small>
            <span>{{ member.note }}</span>
          </div>

          <!-- 第一层:红线(过敏) + 第二层:长期忌口 -->
          <div v-if="member.restrictions.length" class="restriction-tags">
            <el-tag
              v-for="restriction in member.restrictions"
              :key="restriction.id"
              :type="restriction.type === 'allergy' ? 'danger' : 'warning'"
              :effect="restriction.type === 'allergy' ? 'dark' : 'plain'"
              size="small"
            >
              {{ restriction.type === 'allergy' ? '过敏' : '忌口' }}·{{
                restriction.ingredient_name
              }}
            </el-tag>
          </div>
          <p v-else class="no-restriction">暂无过敏或忌口</p>

          <div v-if="member.food_preferences?.length" class="restriction-tags food-preference-tags">
            <el-tag
              v-for="preference in member.food_preferences"
              :key="preference.id"
              type="info"
              effect="plain"
              size="small"
              closable
              @close="removeFoodPreference(preference.id)"
            >
              {{ preference.preference === 'dislike' ? '不喜欢' : '喜欢' }}·{{
                preference.target_name
              }}
            </el-tag>
          </div>

          <!-- 第三层:临时特殊事件(带时间限定) -->
          <div v-if="memberEvents(member.user_id).length" class="event-block">
            <div
              v-for="event in memberEvents(member.user_id)"
              :key="event.id"
              class="event-line"
              :class="eventState(event)"
            >
              <span class="event-dot"></span>
              <span class="event-main">
                {{ event.event_type }}
                <em v-if="eventState(event) === 'active'">生效中</em>
                <em v-else-if="eventState(event) === 'future'" class="future">未开始</em>
                <em v-else class="expired">已结束</em>
              </span>
              <small>{{ event.constraint_tags.join('、') || '无附加约束' }}</small>
              <small class="event-date">{{ event.start_date }} ~ {{ event.end_date }}</small>
            </div>
          </div>
        </div>
      </article>

      <div v-if="!loading && !filteredMembers.length" class="empty-state">
        <el-icon><Plus /></el-icon>
        <h3>还没有匹配的家庭成员</h3>
        <p>换个关键词，或新增一位家人的饮食档案。</p>
        <el-button type="primary" @click="openCreate">新增人员</el-button>
      </div>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑成员档案' : '新增人员'"
      width="580px"
      class="member-dialog"
      destroy-on-close
      @closed="resetDrafts"
    >
      <el-tabs v-model="editTab" class="member-tabs">
        <el-tab-pane label="基础档案" name="profile">
          <el-form label-position="top">
            <div class="form-grid two">
              <el-form-item label="名字" required>
                <el-input
                  v-model="form.display_name"
                  maxlength="20"
                  placeholder="怎么称呼这位成员"
                />
              </el-form-item>
              <el-form-item label="性别">
                <el-select v-model="form.gender" style="width: 100%">
                  <el-option v-for="g in GENDERS" :key="g" :value="g" :label="g" />
                </el-select>
              </el-form-item>
            </div>
            <el-form-item label="身份">
              <el-radio-group v-model="form.residency">
                <el-radio-button value="resident">常驻成员</el-radio-button>
                <el-radio-button value="guest">访客</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="自由描述(选填)">
              <el-input
                v-model="form.taboo"
                maxlength="100"
                placeholder="例如:不吃香菜、海鲜过敏"
              />
            </el-form-item>
            <el-form-item label="备注(选填)">
              <el-input
                v-model="form.note"
                type="textarea"
                :rows="2"
                maxlength="200"
                placeholder="例如:周末偶尔来吃饭"
              />
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="过敏与忌口" name="restriction">
          <p class="tab-hint">红线(过敏)为最高约束强制过滤;长期忌口会被智能规避或等价替换。</p>
          <div class="draft-row">
            <el-select v-model="restrictionDraft.type" style="width: 110px">
              <el-option value="allergy" label="🔴 过敏" />
              <el-option value="dislike" label="🟠 忌口" />
            </el-select>
            <el-select
              v-model="restrictionDraft.ingredient_key"
              filterable
              placeholder="选择食材"
              style="flex: 1"
            >
              <el-option-group
                v-for="[category, options] in groupedIngredients"
                :key="category"
                :label="category"
              >
                <el-option
                  v-for="option in options"
                  :key="option.key"
                  :value="option.key"
                  :label="option.name"
                />
              </el-option-group>
            </el-select>
            <el-button type="primary" plain @click="stageRestriction">加入</el-button>
          </div>

          <div class="tag-list">
            <el-tag
              v-for="restriction in existingRestrictions"
              :key="restriction.id"
              :type="restriction.type === 'allergy' ? 'danger' : 'warning'"
              :effect="restriction.type === 'allergy' ? 'dark' : 'plain'"
              closable
              @close="removeRestriction(restriction.id)"
            >
              {{ restriction.type === 'allergy' ? '过敏' : '忌口' }}·{{
                restriction.ingredient_name
              }}
            </el-tag>
            <el-tag
              v-for="(item, index) in pendingRestrictions"
              :key="`pending-${index}`"
              :type="item.type === 'allergy' ? 'danger' : 'warning'"
              :effect="item.type === 'allergy' ? 'dark' : 'plain'"
              closable
              @close="removePendingRestriction(index)"
            >
              {{ item.type === 'allergy' ? '过敏' : '忌口' }}·{{ item.name }}(待保存)
            </el-tag>
            <p
              v-if="!existingRestrictions.length && !pendingRestrictions.length"
              class="no-restriction"
            >
              还没有记录过敏或忌口
            </p>
          </div>
        </el-tab-pane>

        <el-tab-pane label="特殊事件" name="event">
          <p class="tab-hint">🟡 临时事件仅在生效期内参与约束,过期自动失效,不写入长期档案。</p>
          <el-form label-position="top">
            <el-form-item label="事件类型">
              <el-select
                v-model="eventDraft.event_type"
                style="width: 100%"
                @change="onEventTypeChange"
              >
                <el-option
                  v-for="preset in EVENT_PRESETS"
                  :key="preset.type"
                  :value="preset.type"
                  :label="preset.type"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="生效日期(必填)">
              <el-date-picker
                v-model="eventDraft.range"
                type="daterange"
                start-placeholder="开始"
                end-placeholder="结束"
                value-format="YYYY-MM-DD"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="饮食约束(可多选)">
              <el-select
                v-model="eventDraft.constraint_tags"
                multiple
                collapse-tags
                collapse-tags-tooltip
                placeholder="选择或补充约束"
                style="width: 100%"
              >
                <el-option v-for="tag in TAG_OPTIONS" :key="tag" :value="tag" :label="tag" />
              </el-select>
            </el-form-item>
            <el-form-item label="备注(选填)">
              <el-input
                v-model="eventDraft.note"
                maxlength="100"
                placeholder="例如:昨天开始嗓子疼"
              />
            </el-form-item>
          </el-form>
          <el-button type="primary" plain @click="stageEvent">记录这次事件</el-button>

          <div class="event-history">
            <div
              v-for="event in existingEvents"
              :key="event.id"
              class="event-line"
              :class="eventState(event)"
            >
              <span class="event-dot"></span>
              <span class="event-main">
                {{ event.event_type }}
                <em v-if="eventState(event) === 'active'">生效中</em>
                <em v-else-if="eventState(event) === 'future'" class="future">未开始</em>
                <em v-else class="expired">已结束</em>
              </span>
              <small>{{ event.constraint_tags.join('、') || '无附加约束' }}</small>
              <small class="event-date">{{ event.start_date }} ~ {{ event.end_date }}</small>
              <el-button
                :icon="Delete"
                size="small"
                circle
                text
                type="danger"
                @click="removeEvent(event.id)"
              />
            </div>
            <div
              v-for="(item, index) in pendingEvents"
              :key="`pending-event-${index}`"
              class="event-line pending"
            >
              <span class="event-dot"></span>
              <span class="event-main">{{ item.event_type }} <em class="future">待保存</em></span>
              <small>{{ item.constraint_tags.join('、') || '无附加约束' }}</small>
              <small class="event-date">{{ item.start }} ~ {{ item.end }}</small>
              <el-button
                :icon="Delete"
                size="small"
                circle
                text
                type="danger"
                @click="removePendingEvent(index)"
              />
            </div>
            <p v-if="!existingEvents.length && !pendingEvents.length" class="no-restriction">
              还没有特殊事件记录
            </p>
          </div>
        </el-tab-pane>
      </el-tabs>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.members-page {
  max-width: 1180px;
  width: 100%;
  height: calc(100vh - 118px);
  min-height: 0;
  margin: 0 auto;
  padding-bottom: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.legend-row {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin: 16px 4px 0;
  font-size: 12.5px;
  color: var(--muted);
}
.legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.legend .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.legend .dot.red {
  background: #c0392b;
}
.legend .dot.orange {
  background: #d79a3c;
}
.legend .dot.yellow {
  background: #e0c05a;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  align-content: start;
  padding: 0 4px 18px 0;
  scrollbar-gutter: stable;
}
.member-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 16px 18px;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}
.member-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 14px 34px rgba(25, 66, 58, 0.12);
}
.card-top {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  margin-bottom: 0.75rem;
}
.avatar {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: #fff;
  flex-shrink: 0;
}
.avatar.resident {
  background: linear-gradient(135deg, #0f4c45, #2e7a6d);
}
.avatar.guest {
  background: linear-gradient(135deg, #c88a2d, #e0b15e);
}
.who {
  flex: 1;
  min-width: 0;
}
.who strong {
  font-size: 15.5px;
}
.who-tags {
  display: flex;
  gap: 4px;
  margin-top: 3px;
}
.card-actions {
  flex-shrink: 0;
}
.card-body {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.info-line {
  font-size: 13px;
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
}
.info-line small {
  flex-shrink: 0;
  color: #9aa8a4;
  background: #f2f4f0;
  padding: 1px 7px;
  border-radius: 6px;
}
.taboo-text {
  color: #a43c36;
}
.restriction-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.no-restriction {
  font-size: 12.5px;
  color: #b3bdb9;
  margin: 0;
}
.event-block {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin-top: 0.15rem;
}
.event-line {
  font-size: 12.5px;
  color: #8a5a12;
  background: #fdf6e8;
  border-radius: 8px;
  padding: 5px 9px;
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}
.event-line.active {
  background: #fdf2d9;
  border: 1px solid #ecd9a8;
}
.event-line.future {
  background: #f3f6f4;
  color: #7d8a86;
}
.event-line.expired {
  background: #f6f6f4;
  color: #a8b0ad;
  opacity: 0.85;
}
.event-line.pending {
  border: 1px dashed #d8c79a;
}
.event-line small {
  color: #b09a6e;
}
.event-line .event-date {
  font-variant-numeric: tabular-nums;
}
.event-main {
  font-weight: 650;
}
.event-main em {
  font-style: normal;
  font-size: 11px;
  padding: 0 5px;
  border-radius: 999px;
  background: #e7c574;
  color: #5f4410;
  margin-left: 4px;
}
.event-main em.future {
  background: #dfe6e3;
  color: #63706c;
}
.event-main em.expired {
  background: #e9eae7;
  color: #98a09d;
}
.event-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e0a23c;
  flex-shrink: 0;
}
.event-line.active .event-dot {
  animation: blink 1.4s ease-in-out infinite;
}
@keyframes blink {
  0%,
  100% {
    opacity: 0.35;
  }
  50% {
    opacity: 1;
  }
}

.empty-state {
  grid-column: 1 / -1;
}
.pantry-card {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(300px, 1.2fr);
  gap: 12px 20px;
  margin: 0 0 14px;
  padding: 14px 16px;
  border: 1px solid #d9e6df;
  border-radius: 12px;
  background: #fbfdfb;
}
.pantry-card p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}
.pantry-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pantry-controls .el-input {
  flex: 1;
}
.pantry-tags {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.tab-hint {
  margin: 0 0 14px;
  font-size: 12.5px;
  color: var(--muted);
  line-height: 1.7;
}
.draft-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
}
.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  min-height: 28px;
}
.event-history {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  margin-top: 16px;
}
.member-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

@media (max-width: 1180px) {
  .card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pantry-card {
    grid-template-columns: 1fr;
  }
}
</style>
