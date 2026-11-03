<script lang="ts">
import { defineComponent, markRaw } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Calendar,
  Clock,
  Delete,
  Dish,
  EditPen,
  Plus,
  Search,
  StarFilled,
  User,
} from '@element-plus/icons-vue'
import {
  createCookingRecord,
  deleteCookingRecord,
  fetchCookingRecords,
  updateCookingRecord,
  type CookingRecordPayload,
} from '../../api/cooking'
interface CookingStep {
  id: string
  title: string
  detail: string
  minutes: number | null
}
interface DishReview {
  id: string
  nickname: string
  rating: number
  content: string
  createdAt: string
}
interface CookingRecord {
  id: string
  image: string
  dishName: string
  cook: string
  cookedAt: string
  servings: number
  durationMinutes: number
  difficulty: string
  tags: string[]
  ingredients: string
  story: string
  steps: CookingStep[]
  rating: number
  nextTimeNote: string
  reviews: DishReview[]
  createdAt: string
}
interface RecordDraft {
  image: string
  dishName: string
  cook: string
  cookedAt: string
  servings: number
  durationMinutes: number
  difficulty: string
  tagsText: string
  ingredients: string
  story: string
  steps: CookingStep[]
  rating: number
  nextTimeNote: string
}
const phoneShotDefaults: Record<string, string> = {
  豆角焖面: '/images/cooking-history/green-bean-braised-noodles-phone.png',
}

export default defineComponent({
  name: 'CookingHistoryView',
  components: { Calendar, Clock, Delete, Dish, EditPen, Plus, Search, StarFilled, User },
  data() {
    return {
      records: [] as CookingRecord[],
      searchText: '',
      activeTag: '全部',
      dialogVisible: false,
      detailVisible: false,
      editingId: null as string | null,
      activeRecord: null as CookingRecord | null,
      reviewForm: { nickname: '', rating: 5, content: '' },
      form: this.emptyDraft() as RecordDraft,
      Calendar: markRaw(Calendar),
      Clock: markRaw(Clock),
      Delete: markRaw(Delete),
      Dish: markRaw(Dish),
      EditPen: markRaw(EditPen),
      Plus: markRaw(Plus),
      Search: markRaw(Search),
      StarFilled: markRaw(StarFilled),
      User: markRaw(User),
    }
  },
  computed: {
    allTags() {
      const values = this.records.flatMap((record) => record.tags)
      return ['全部', ...Array.from(new Set(values)).slice(0, 8)]
    },
    filteredRecords() {
      const keyword = this.searchText.trim().toLowerCase()
      return [...this.records]
        .filter((record) => this.activeTag === '全部' || record.tags.includes(this.activeTag))
        .filter(
          (record) =>
            !keyword ||
            `${record.dishName}${record.cook}${record.tags.join('')}`
              .toLowerCase()
              .includes(keyword),
        )
        .sort((a, b) => b.cookedAt.localeCompare(a.cookedAt))
    },
    stats() {
      const ratings = this.records.map(this.overallRating)
      const month = new Date().toISOString().slice(0, 7)
      return {
        total: this.records.length,
        cooks: new Set(this.records.map((item) => item.cook)).size,
        average: ratings.length
          ? (ratings.reduce((sum, value) => sum + value, 0) / ratings.length).toFixed(1)
          : '—',
        month: this.records.filter((item) => item.cookedAt.startsWith(month)).length,
      }
    },
  },
  methods: {
    uid() {
      return typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`
    },
    localDateTime() {
      const date = new Date()
      date.setMinutes(date.getMinutes() - date.getTimezoneOffset())
      return date.toISOString().slice(0, 16)
    },
    emptyStep(order = 1): CookingStep {
      return { id: this.uid(), title: `步骤 ${order}`, detail: '', minutes: null }
    },
    emptyDraft(): RecordDraft {
      return {
        image: '',
        dishName: '',
        cook: '',
        cookedAt: this.localDateTime(),
        servings: 2,
        durationMinutes: 30,
        difficulty: '家常',
        tagsText: '',
        ingredients: '',
        story: '',
        steps: [this.emptyStep(1)],
        rating: 5,
        nextTimeNote: '',
      }
    },
    placeholder(title: string, from: string, to: string) {
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${from}"/><stop offset="1" stop-color="${to}"/></linearGradient></defs><rect width="1200" height="760" rx="36" fill="url(#g)"/><circle cx="980" cy="130" r="180" fill="rgba(255,255,255,.1)"/><circle cx="160" cy="680" r="220" fill="rgba(255,255,255,.08)"/><text x="70" y="600" fill="white" font-size="76" font-family="sans-serif" font-weight="700">${title}</text><text x="74" y="670" fill="rgba(255,255,255,.72)" font-size="28" font-family="sans-serif">每一次下厨，都值得被记住</text></svg>`
      return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`
    },
    fallbackCover(title: string) {
      return phoneShotDefaults[title] || this.placeholder(title, '#315d54', '#c69b53')
    },
    fromApi(record: CookingRecordPayload): CookingRecord {
      return {
        id: record.id ?? this.uid(),
        image: record.image || this.fallbackCover(record.dish_name),
        dishName: record.dish_name,
        cook: record.cook,
        cookedAt: record.cooked_at,
        servings: record.servings,
        durationMinutes: record.duration_minutes,
        difficulty: record.difficulty,
        tags: record.tags,
        ingredients: record.ingredients,
        story: record.story,
        steps: record.steps.map((step) => ({ ...step, id: this.uid() })),
        rating: record.rating,
        nextTimeNote: record.next_time_note,
        reviews: record.reviews ?? [],
        createdAt: new Date().toISOString(),
      }
    },
    toApi(record: CookingRecord | RecordDraft): CookingRecordPayload {
      return {
        id: 'id' in record ? record.id : undefined,
        dish_name: record.dishName,
        cook: record.cook,
        cooked_at: record.cookedAt,
        servings: record.servings,
        duration_minutes: record.durationMinutes,
        difficulty: record.difficulty,
        tags:
          'tags' in record
            ? record.tags
            : record.tagsText
                .split(/[、,，]/)
                .map((item) => item.trim())
                .filter(Boolean),
        ingredients: record.ingredients,
        story: record.story,
        steps: record.steps.map(({ title, detail, minutes }) => ({ title, detail, minutes })),
        rating: record.rating,
        next_time_note: record.nextTimeNote,
        image: record.image,
        reviews: 'reviews' in record ? record.reviews : [],
      }
    },
    async load() {
      try {
        const remote = await fetchCookingRecords()
        this.records = remote.map(this.fromApi)
      } catch {
        this.records = []
        ElMessage.error('下厨足迹加载失败，请检查后端连接')
      }
    },
    overallRating(record: CookingRecord) {
      const ratings = [record.rating, ...record.reviews.map((review) => review.rating)]
      return ratings.reduce((sum, value) => sum + value, 0) / ratings.length
    },
    formatDate(value: string) {
      return new Intl.DateTimeFormat('zh-CN', {
        month: 'long',
        day: 'numeric',
        weekday: 'short',
      }).format(new Date(value))
    },
    openCreate() {
      this.editingId = null
      Object.assign(this.form, this.emptyDraft())
      this.dialogVisible = true
    },
    openEdit(record: CookingRecord) {
      this.editingId = record.id
      Object.assign(this.form, {
        ...record,
        tagsText: record.tags.join('、'),
        steps: record.steps.map((step) => ({ ...step })),
      })
      this.detailVisible = false
      this.dialogVisible = true
    },
    openDetail(record: CookingRecord) {
      this.activeRecord = record
      Object.assign(this.reviewForm, { nickname: '', rating: 5, content: '' })
      this.detailVisible = true
    },
    addStep() {
      this.form.steps.push(this.emptyStep(this.form.steps.length + 1))
    },
    removeStep(index: number) {
      if (this.form.steps.length === 1) {
        ElMessage.warning('至少保留一个步骤')
        return
      }
      this.form.steps.splice(index, 1)
    },
    chooseImage() {
      ;(this.$refs.imageInput as HTMLInputElement | undefined)?.click()
    },
    resizeImage(file: File): Promise<string> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onerror = () => reject(new Error('图片读取失败'))
        reader.onload = () => {
          const image = new Image()
          image.onerror = () => reject(new Error('图片格式不受支持'))
          image.onload = () => {
            const maxWidth = 1400
            const scale = Math.min(1, maxWidth / image.width)
            const canvas = document.createElement('canvas')
            canvas.width = Math.round(image.width * scale)
            canvas.height = Math.round(image.height * scale)
            const context = canvas.getContext('2d')
            if (!context) return reject(new Error('图片处理失败'))
            context.drawImage(image, 0, 0, canvas.width, canvas.height)
            resolve(canvas.toDataURL('image/jpeg', 0.82))
          }
          image.src = String(reader.result)
        }
        reader.readAsDataURL(file)
      })
    },
    async onImageChange(event: Event) {
      const input = event.target as HTMLInputElement
      const file = input.files?.[0]
      if (!file) return
      if (!file.type.startsWith('image/')) return ElMessage.warning('请选择图片文件')
      if (file.size > 8 * 1024 * 1024) return ElMessage.warning('图片不能超过 8MB')
      try {
        this.form.image = await this.resizeImage(file)
      } catch (error) {
        ElMessage.error(error instanceof Error ? error.message : '图片处理失败')
      } finally {
        input.value = ''
      }
    },
    async submitRecord() {
      if (!this.form.dishName.trim() || !this.form.cook.trim()) {
        ElMessage.warning('请填写菜名和做菜人')
        return
      }
      const validSteps = this.form.steps.filter((step) => step.detail.trim())
      if (!validSteps.length) {
        ElMessage.warning('至少记录一个做菜步骤')
        return
      }
      const existing = this.records.find((item) => item.id === this.editingId)
      const record: CookingRecord = {
        id: existing?.id ?? this.uid(),
        image: this.form.image || this.fallbackCover(this.form.dishName.trim()),
        dishName: this.form.dishName.trim(),
        cook: this.form.cook.trim(),
        cookedAt: this.form.cookedAt,
        servings: this.form.servings,
        durationMinutes: this.form.durationMinutes,
        difficulty: this.form.difficulty,
        tags: this.form.tagsText
          .split(/[、,，]/)
          .map((item) => item.trim())
          .filter(Boolean),
        ingredients: this.form.ingredients.trim(),
        story: this.form.story.trim(),
        steps: validSteps.map((step, index) => ({
          ...step,
          title: step.title.trim() || `步骤 ${index + 1}`,
        })),
        rating: this.form.rating,
        nextTimeNote: this.form.nextTimeNote.trim(),
        reviews: existing?.reviews ?? [],
        createdAt: existing?.createdAt ?? new Date().toISOString(),
      }
      if (existing) {
        const updated = await updateCookingRecord(existing.id, this.toApi(record))
        Object.assign(existing, {
          ...record,
          ...this.fromApi(updated),
          image: existing.image,
          reviews: existing.reviews,
        })
      } else {
        const created = await createCookingRecord(this.toApi(record))
        this.records.unshift({
          ...record,
          ...this.fromApi(created),
          image: record.image,
          reviews: record.reviews,
        })
      }
      this.dialogVisible = false
      ElMessage.success(existing ? '下厨记录已更新' : '这一餐已经收进下厨足迹')
    },
    async removeRecord(record: CookingRecord) {
      try {
        await ElMessageBox.confirm(`确定删除「${record.dishName}」的下厨记录吗？`, '删除记录', {
          type: 'warning',
          confirmButtonText: '删除',
          cancelButtonText: '取消',
        })
      } catch {
        return
      }
      await deleteCookingRecord(record.id)
      this.records = this.records.filter((item) => item.id !== record.id)
      if (this.activeRecord?.id === record.id) this.detailVisible = false
      ElMessage.success('记录已删除')
    },
    async submitReview() {
      if (!this.activeRecord) return
      if (!this.reviewForm.content.trim()) {
        ElMessage.warning('写一句评价再提交吧')
        return
      }
      this.activeRecord.reviews.unshift({
        id: this.uid(),
        nickname: this.reviewForm.nickname.trim() || '匿名试吃员',
        rating: this.reviewForm.rating,
        content: this.reviewForm.content.trim(),
        createdAt: new Date().toISOString(),
      })
      try {
        await updateCookingRecord(this.activeRecord.id, { reviews: this.activeRecord.reviews })
      } catch {
        ElMessage.error('评价同步失败，请检查后端连接')
        return
      }
      Object.assign(this.reviewForm, { nickname: '', rating: 5, content: '' })
      ElMessage.success('评价已留下，等账户功能上线后可自动关联身份')
    },
  },
  mounted() {
    return this.load()
  },
})
</script>

<template>
  <section class="footprint-page">
    <header class="page-hero">
      <div>
        <span class="eyebrow">COOKING JOURNAL</span>
        <h2>下厨足迹</h2>
        <p>留住每一次烟火气，也记下下一次会做得更好的地方。</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新增菜单</el-button>
    </header>

    <div class="summary-strip">
      <div>
        <span>做过的菜</span><strong>{{ stats.total }}</strong
        ><small>道记录</small>
      </div>
      <div>
        <span>本月下厨</span><strong>{{ stats.month }}</strong
        ><small>次</small>
      </div>
      <div>
        <span>平均评分</span><strong>{{ stats.average }}</strong
        ><small>/ 5</small>
      </div>
      <div>
        <span>掌勺人</span><strong>{{ stats.cooks }}</strong
        ><small>位</small>
      </div>
    </div>

    <div class="filter-bar">
      <el-input
        v-model="searchText"
        :prefix-icon="Search"
        clearable
        placeholder="搜索菜名、做菜人或标签"
      />
      <div class="tag-filter">
        <button
          v-for="tag in allTags"
          :key="tag"
          :class="{ active: activeTag === tag }"
          @click="activeTag = tag"
        >
          {{ tag }}
        </button>
      </div>
    </div>

    <div v-if="filteredRecords.length" class="record-grid">
      <article
        v-for="record in filteredRecords"
        :key="record.id"
        class="record-card"
        @click="openDetail(record)"
      >
        <div class="cover-wrap">
          <img :src="record.image" :alt="record.dishName" />
          <span class="difficulty">{{ record.difficulty }}</span>
          <div class="rating-badge">
            <el-icon><StarFilled /></el-icon>{{ overallRating(record).toFixed(1) }}
          </div>
        </div>
        <div class="card-content">
          <div class="card-title">
            <h3>{{ record.dishName }}</h3>
            <span>{{ formatDate(record.cookedAt) }}</span>
          </div>
          <p v-if="record.story" class="story">{{ record.story }}</p>
          <div class="card-meta">
            <span
              ><el-icon><User /></el-icon>{{ record.cook }}</span
            >
            <span
              ><el-icon><Clock /></el-icon>{{ record.durationMinutes }} 分钟</span
            >
            <span
              ><el-icon><Dish /></el-icon>{{ record.servings }} 人份</span
            >
          </div>
          <div class="card-footer">
            <div class="mini-tags">
              <span v-for="tag in record.tags.slice(0, 3)" :key="tag"># {{ tag }}</span>
            </div>
            <small>{{ record.reviews.length }} 条评价</small>
          </div>
        </div>
      </article>
    </div>
    <div v-else class="empty-state">
      <el-icon><Dish /></el-icon>
      <h3>还没有找到下厨记录</h3>
      <p>换个关键词，或者记录今天做的第一道菜。</p>
      <el-button type="primary" @click="openCreate">新增菜单</el-button>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑下厨记录' : '新增菜单'"
      width="760px"
      class="record-dialog"
      destroy-on-close
    >
      <div class="dialog-scroll">
        <section
          class="form-section cover-editor"
          :class="{ filled: form.image }"
          @click="chooseImage"
        >
          <img v-if="form.image" :src="form.image" alt="菜品主图预览" />
          <div v-else>
            <el-icon><Plus /></el-icon><strong>添加菜单主图片</strong
            ><small>点击上传，建议横向照片，最大 8MB</small>
          </div>
          <div v-if="form.image" class="cover-mask">点击更换图片</div>
          <input ref="imageInput" type="file" accept="image/*" hidden @change="onImageChange" />
        </section>

        <section class="form-section">
          <div class="section-label">
            <span>01</span>
            <div><strong>这道菜是谁做的</strong><small>基础信息</small></div>
          </div>
          <el-form label-position="top">
            <div class="form-grid two">
              <el-form-item label="菜名" required
                ><el-input v-model="form.dishName" maxlength="30" placeholder="例如：番茄牛腩"
              /></el-form-item>
              <el-form-item label="做菜人" required
                ><el-input v-model="form.cook" maxlength="20" placeholder="例如：Colin"
              /></el-form-item>
              <el-form-item label="下厨时间"
                ><el-date-picker
                  v-model="form.cookedAt"
                  type="datetime"
                  value-format="YYYY-MM-DDTHH:mm"
                  format="YYYY年MM月DD日 HH:mm"
                  style="width: 100%"
              /></el-form-item>
              <el-form-item label="难度"
                ><el-select v-model="form.difficulty" style="width: 100%"
                  ><el-option
                    v-for="item in ['快手', '家常', '进阶', '挑战']"
                    :key="item"
                    :label="item"
                    :value="item" /></el-select
              ></el-form-item>
              <el-form-item label="用时（分钟）"
                ><el-input-number
                  v-model="form.durationMinutes"
                  :min="1"
                  :max="600"
                  style="width: 100%"
              /></el-form-item>
              <el-form-item label="份量（人）"
                ><el-input-number v-model="form.servings" :min="1" :max="50" style="width: 100%"
              /></el-form-item>
            </div>
            <el-form-item label="标签"
              ><el-input
                v-model="form.tagsText"
                placeholder="用逗号分隔，例如：周末下厨、暖胃、第一次尝试"
            /></el-form-item>
            <el-form-item label="这一餐的故事"
              ><el-input
                v-model="form.story"
                type="textarea"
                :rows="2"
                maxlength="240"
                show-word-limit
                placeholder="为什么做这道菜，和谁一起吃，有什么值得记住的？"
            /></el-form-item>
          </el-form>
        </section>

        <section class="form-section">
          <div class="section-label">
            <span>02</span>
            <div><strong>食材准备</strong><small>一行一种或自由记录</small></div>
          </div>
          <el-input
            v-model="form.ingredients"
            type="textarea"
            :rows="3"
            placeholder="例如：牛腩 600g、番茄 4 个、土豆 2 个……"
          />
        </section>

        <section class="form-section">
          <div class="section-label action-label">
            <span>03</span>
            <div><strong>详细过程</strong><small>把容易忘的火候和细节留下来</small></div>
            <el-button text type="primary" :icon="Plus" @click="addStep">添加步骤</el-button>
          </div>
          <div class="step-editor-list">
            <div v-for="(step, index) in form.steps" :key="step.id" class="step-editor">
              <b>{{ String(index + 1).padStart(2, '0') }}</b>
              <div class="step-fields">
                <el-input v-model="step.title" maxlength="30" placeholder="步骤标题" />
                <el-input
                  v-model="step.detail"
                  type="textarea"
                  :rows="2"
                  maxlength="300"
                  placeholder="详细记录怎么做、火候和判断标准"
                />
              </div>
              <el-input-number
                v-model="step.minutes"
                :min="1"
                :max="300"
                placeholder="分钟"
                controls-position="right"
              />
              <el-button :icon="Delete" circle text type="danger" @click="removeStep(index)" />
            </div>
          </div>
        </section>

        <section class="form-section rating-section">
          <div class="section-label">
            <span>04</span>
            <div><strong>给自己的作品打分</strong><small>满分 5 颗星</small></div>
          </div>
          <el-rate
            v-model="form.rating"
            allow-half
            size="large"
            :colors="['#d7a84d', '#d7a84d', '#d7a84d']"
          />
          <el-input
            v-model="form.nextTimeNote"
            type="textarea"
            :rows="2"
            maxlength="200"
            placeholder="下次准备怎么改进？例如：盐少一点，炖煮再多十分钟。"
          />
        </section>
      </div>
      <template #footer
        ><el-button @click="dialogVisible = false">取消</el-button
        ><el-button type="primary" @click="submitRecord">保存到下厨足迹</el-button></template
      >
    </el-dialog>

    <el-drawer v-model="detailVisible" size="680px" class="detail-drawer" destroy-on-close>
      <template #header
        ><div class="drawer-title">
          <span>COOKING STORY</span>
          <h2>{{ activeRecord?.dishName }}</h2>
        </div></template
      >
      <template v-if="activeRecord">
        <img class="detail-cover" :src="activeRecord.image" :alt="activeRecord.dishName" />
        <div class="detail-actions">
          <div>
            <el-rate :model-value="overallRating(activeRecord)" disabled allow-half /><span
              >{{ overallRating(activeRecord).toFixed(1) }} ·
              {{ activeRecord.reviews.length + 1 }} 人评分</span
            >
          </div>
          <div>
            <el-button :icon="EditPen" @click="openEdit(activeRecord)">编辑</el-button
            ><el-button :icon="Delete" type="danger" plain @click="removeRecord(activeRecord)"
              >删除</el-button
            >
          </div>
        </div>
        <p class="detail-story">
          {{ activeRecord.story || '这次下厨没有留下文字，不过味道已经被记住了。' }}
        </p>
        <div class="detail-facts">
          <div>
            <el-icon><User /></el-icon><span>做菜人</span><strong>{{ activeRecord.cook }}</strong>
          </div>
          <div>
            <el-icon><Calendar /></el-icon><span>下厨时间</span
            ><strong>{{ formatDate(activeRecord.cookedAt) }}</strong>
          </div>
          <div>
            <el-icon><Clock /></el-icon><span>用时</span
            ><strong>{{ activeRecord.durationMinutes }} 分钟</strong>
          </div>
          <div>
            <el-icon><Dish /></el-icon><span>份量</span
            ><strong>{{ activeRecord.servings }} 人份</strong>
          </div>
        </div>
        <section class="detail-section">
          <h3>食材准备</h3>
          <p class="ingredient-note">{{ activeRecord.ingredients || '没有记录食材用量' }}</p>
        </section>
        <section class="detail-section">
          <h3>下厨过程</h3>
          <div class="timeline">
            <div v-for="(step, index) in activeRecord.steps" :key="step.id" class="timeline-step">
              <span>{{ index + 1 }}</span>
              <div>
                <div class="timeline-title">
                  <strong>{{ step.title }}</strong
                  ><small v-if="step.minutes">{{ step.minutes }} 分钟</small>
                </div>
                <p>{{ step.detail }}</p>
              </div>
            </div>
          </div>
        </section>
        <section v-if="activeRecord.nextTimeNote" class="next-note">
          <strong>下次会做得更好</strong>
          <p>{{ activeRecord.nextTimeNote }}</p>
        </section>
        <section class="detail-section reviews">
          <h3>
            大家怎么说 <small>{{ activeRecord.reviews.length }}</small>
          </h3>
          <div v-for="review in activeRecord.reviews" :key="review.id" class="review-item">
            <div class="review-avatar">{{ review.nickname.slice(0, 1) }}</div>
            <div>
              <div>
                <strong>{{ review.nickname }}</strong
                ><el-rate :model-value="review.rating" disabled size="small" />
              </div>
              <p>{{ review.content }}</p>
              <small>{{ new Date(review.createdAt).toLocaleString('zh-CN') }}</small>
            </div>
          </div>
          <div v-if="!activeRecord.reviews.length" class="no-review">
            还没有其他人评价，来做第一个试吃员吧。
          </div>
        </section>
        <section class="review-form">
          <div>
            <h3>评价这道菜</h3>
            <small>账户功能尚未开放，目前可使用昵称评价</small>
          </div>
          <div class="review-line">
            <el-input
              v-model="reviewForm.nickname"
              maxlength="16"
              placeholder="昵称（可不填）"
            /><el-rate v-model="reviewForm.rating" />
          </div>
          <el-input
            v-model="reviewForm.content"
            type="textarea"
            :rows="3"
            maxlength="180"
            show-word-limit
            placeholder="味道、口感，或者想对做菜人说的话……"
          /><el-button type="primary" @click="submitReview">提交评价</el-button>
        </section>
      </template>
    </el-drawer>
  </section>
</template>

<style scoped>
.footprint-page {
  max-width: 1180px;
  margin: 0 auto;
  padding-bottom: 40px;
}
.page-hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  padding: 20px 136px 24px 4px;
}
.page-hero h2 {
  margin: 5px 0 7px;
  font:
    34px Georgia,
    'Songti SC',
    serif;
}
.page-hero p {
  margin: 0;
  color: var(--muted);
}
.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 18px;
  margin-bottom: 18px;
  overflow: hidden;
}
.summary-strip > div {
  padding: 20px 24px;
  border-right: 1px solid #e9ece8;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: end;
  gap: 7px;
}
.summary-strip > div:last-child {
  border: 0;
}
.summary-strip span,
.summary-strip small {
  color: var(--muted);
  font-size: 12px;
}
.summary-strip strong {
  font:
    30px Georgia,
    serif;
  color: var(--green);
}
.filter-bar {
  display: flex;
  gap: 16px;
  align-items: center;
  margin: 18px 0;
}
.filter-bar > .el-input {
  width: 290px;
}
.tag-filter {
  display: flex;
  gap: 7px;
  overflow-x: auto;
}
.tag-filter button {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--muted);
  padding: 7px 12px;
  border-radius: 999px;
  white-space: nowrap;
}
.tag-filter button.active {
  background: var(--green);
  border-color: var(--green);
  color: #fff;
}
.record-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.record-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 16px;
  overflow: hidden;
  cursor: pointer;
  transition: 0.22s ease;
}
.record-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 16px 38px rgba(25, 66, 58, 0.12);
}
.cover-wrap {
  height: 150px;
  position: relative;
  overflow: hidden;
}
.cover-wrap img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.35s ease;
}
.record-card:hover img {
  transform: scale(1.035);
}
.difficulty,
.rating-badge {
  position: absolute;
  top: 10px;
  border-radius: 999px;
  backdrop-filter: blur(12px);
}
.difficulty {
  left: 10px;
  padding: 4px 9px;
  background: rgba(255, 255, 255, 0.88);
  font-size: 11px;
}
.rating-badge {
  right: 10px;
  padding: 4px 8px;
  background: rgba(20, 54, 48, 0.88);
  color: #fff;
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
}
.rating-badge .el-icon {
  color: #f1c764;
}
.card-content {
  padding: 13px 14px 12px;
}
.card-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
}
.card-title h3 {
  margin: 0;
  font:
    19px Georgia,
    'Songti SC',
    serif;
}
.card-title span {
  font-size: 11.5px;
  color: var(--muted);
  white-space: nowrap;
}
.story {
  height: 38px;
  margin: 7px 0;
  color: #66736f;
  font-size: 12.5px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-meta {
  display: flex;
  gap: 12px;
  padding: 9px 0;
  border-top: 1px solid #edf0ed;
  border-bottom: 1px solid #edf0ed;
  color: var(--muted);
  font-size: 11.5px;
}
.card-meta span {
  display: flex;
  align-items: center;
  gap: 4px;
}
.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 9px;
}
.mini-tags {
  display: flex;
  gap: 6px;
  overflow: hidden;
}
.mini-tags span {
  color: #5c7d74;
  background: #edf5f1;
  padding: 3px 6px;
  border-radius: 6px;
  font-size: 10.5px;
  white-space: nowrap;
}
.card-footer small {
  color: #9aa5a1;
  white-space: nowrap;
}
.empty-state {
  padding: 80px;
  text-align: center;
  border: 1px dashed #b8c5bf;
  border-radius: 18px;
  color: var(--muted);
}
.empty-state > .el-icon {
  font-size: 44px;
  color: #86a49a;
}
.empty-state h3 {
  color: var(--ink);
}
.dialog-scroll {
  max-height: 68vh;
  overflow-y: auto;
  padding-right: 8px;
}
.form-section {
  padding: 18px 0;
  border-bottom: 1px solid #edf0ed;
}
.form-section:last-child {
  border: 0;
}
.cover-editor {
  height: 230px;
  border: 1px dashed #aebeb8;
  border-radius: 16px;
  margin-bottom: 10px;
  display: grid;
  place-items: center;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  background: #f3f7f4;
}
.cover-editor > div:first-child {
  display: grid;
  place-items: center;
  gap: 6px;
  color: #678078;
}
.cover-editor > div .el-icon {
  font-size: 30px;
}
.cover-editor small {
  color: #9aa8a3;
}
.cover-editor img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cover-mask {
  position: absolute;
  inset: auto 0 0;
  padding: 12px;
  text-align: center;
  background: rgba(15, 76, 69, 0.78);
  color: #fff;
}
.section-label {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.section-label > span {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: #e8f1ed;
  color: var(--green);
  display: grid;
  place-items: center;
  font-weight: 700;
}
.section-label > div {
  display: grid;
}
.section-label small {
  color: var(--muted);
}
.action-label > .el-button {
  margin-left: auto;
}
.form-grid.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 14px;
}
.step-editor-list {
  display: grid;
  gap: 10px;
}
.step-editor {
  display: grid;
  grid-template-columns: 34px 1fr 110px 34px;
  gap: 9px;
  align-items: start;
  background: #f7f8f5;
  border-radius: 12px;
  padding: 12px;
}
.step-editor > b {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--green);
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 11px;
}
.step-fields {
  display: grid;
  gap: 7px;
}
.rating-section > .el-rate {
  margin: -4px 0 14px;
}
.drawer-title span {
  font-size: 10px;
  letter-spacing: 0.16em;
  color: var(--green-2);
}
.drawer-title h2 {
  font:
    28px Georgia,
    'Songti SC',
    serif;
  margin: 3px 0;
}
.detail-cover {
  width: 100%;
  height: 330px;
  object-fit: cover;
  border-radius: 18px;
}
.detail-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 15px 0;
  border-bottom: 1px solid #e8ebe8;
}
.detail-actions > div {
  display: flex;
  align-items: center;
  gap: 8px;
}
.detail-actions span {
  font-size: 12px;
  color: var(--muted);
}
.detail-story {
  font:
    18px/1.8 Georgia,
    'Songti SC',
    serif;
  color: #465b55;
  padding: 12px 4px;
}
.detail-facts {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.detail-facts > div {
  display: grid;
  gap: 3px;
  padding: 12px;
  background: #f5f6f2;
  border-radius: 11px;
}
.detail-facts .el-icon {
  color: var(--green);
}
.detail-facts span {
  font-size: 11px;
  color: var(--muted);
}
.detail-facts strong {
  font-size: 13px;
}
.detail-section {
  margin-top: 24px;
}
.detail-section h3,
.review-form h3 {
  font:
    20px Georgia,
    'Songti SC',
    serif;
  margin: 0 0 12px;
}
.ingredient-note {
  padding: 15px;
  border-radius: 12px;
  background: #fff8e9;
  color: #695a39;
  line-height: 1.75;
}
.timeline {
  display: grid;
}
.timeline-step {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 12px;
  position: relative;
  padding-bottom: 18px;
}
.timeline-step:before {
  content: '';
  position: absolute;
  left: 16px;
  top: 32px;
  bottom: 0;
  width: 1px;
  background: #d8e2de;
}
.timeline-step:last-child:before {
  display: none;
}
.timeline-step > span {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--green);
  color: #fff;
  display: grid;
  place-items: center;
}
.timeline-title {
  display: flex;
  justify-content: space-between;
}
.timeline-title small {
  color: var(--muted);
}
.timeline-step p {
  margin: 5px 0;
  color: #61726c;
  line-height: 1.65;
}
.next-note {
  margin-top: 20px;
  padding: 16px 18px;
  border-left: 4px solid var(--gold);
  background: #fff9eb;
  border-radius: 8px;
}
.next-note p {
  margin: 6px 0 0;
  color: #6d6249;
}
.reviews h3 small {
  color: var(--muted);
}
.review-item {
  display: grid;
  grid-template-columns: 38px 1fr;
  gap: 10px;
  padding: 13px 0;
  border-bottom: 1px solid #edf0ed;
}
.review-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #dfece7;
  color: var(--green);
  display: grid;
  place-items: center;
  font-weight: 700;
}
.review-item > div > div {
  display: flex;
  justify-content: space-between;
}
.review-item p {
  margin: 5px 0;
  color: #586963;
}
.review-item small,
.no-review {
  color: #9aa5a1;
  font-size: 11px;
}
.no-review {
  padding: 22px;
  text-align: center;
  background: #f7f8f5;
  border-radius: 10px;
}
.review-form {
  margin-top: 25px;
  padding: 18px;
  background: #f3f6f3;
  border-radius: 14px;
  display: grid;
  gap: 12px;
}
.review-form > div:first-child {
  display: flex;
  justify-content: space-between;
}
.review-form h3 {
  margin: 0;
}
.review-form small {
  color: var(--muted);
}
.review-line {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 14px;
  align-items: center;
}
.review-form > .el-button {
  justify-self: end;
}
@media (max-width: 1400px) {
  .record-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 1100px) {
  .record-grid {
    grid-template-columns: 1fr;
  }
  .summary-strip {
    grid-template-columns: repeat(2, 1fr);
  }
  .summary-strip > div:nth-child(2) {
    border-right: 0;
  }
  .summary-strip > div:nth-child(-n + 2) {
    border-bottom: 1px solid #e9ece8;
  }
}
/* 页面固定,仅下厨记录列表滚动 */
.footprint-page {
  height: calc(100vh - 118px);
  min-height: 0;
  padding-bottom: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.footprint-page .record-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  align-content: start;
  padding: 0 4px 18px 0;
  scrollbar-gutter: stable;
}
.cover-wrap {
  background: #e8e4da;
}
.cover-wrap img,
.detail-cover {
  object-position: center 54%;
  filter: saturate(0.94) contrast(0.98) brightness(1.01);
}
</style>
