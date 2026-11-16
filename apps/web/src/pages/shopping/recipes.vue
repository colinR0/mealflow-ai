<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import { setAssistantPrompt } from '../../shared/assistantPrompt'
import { ingredientLabel, type OfflineIngredient } from '../../shared/offlineSearch'
import { session } from '../../shared/session'
import { request } from '../../shared/transport'

interface RecipeSummary {
  id: string
  name: string
  servings: number
  duration_minutes: number
  category: string
  tags: string[]
  taste_tags: string[]
  allergens: string[]
  source_name: string
  source_url: string | null
  license_note: string
  ingredient_names: string[]
  ingredient_count: number
}
interface RecipeDetail extends RecipeSummary {
  steps: string[]
  ingredients: OfflineIngredient[]
}
interface RecipePage {
  items: RecipeSummary[]
  total: number
  has_more: boolean
}

export default defineComponent({
  name: 'RecipeBrowsePage',
  components: { SubpageHeader },
  data() {
    return {
      recipes: [] as RecipeSummary[],
      detail: null as RecipeDetail | null,
      loading: false,
      detailLoading: false,
      error: '',
      search: '',
      category: '全部',
      selectedId: '',
    }
  },
  computed: {
    categories(): string[] {
      return ['全部', ...new Set(this.recipes.map((recipe) => recipe.category || '其他'))]
    },
    filteredRecipes(): RecipeSummary[] {
      const keyword = this.search.trim().toLowerCase()
      return this.recipes.filter((recipe) => {
        if (this.category !== '全部' && (recipe.category || '其他') !== this.category) return false
        if (!keyword) return true
        const haystack = [recipe.name, recipe.category, ...(recipe.tags || []), ...(recipe.ingredient_names || [])]
          .join(' ').toLowerCase()
        return haystack.includes(keyword)
      })
    },
    selectedRecipe(): RecipeSummary | RecipeDetail | null {
      return this.detail || this.recipes.find((recipe) => recipe.id === this.selectedId) || null
    },
    activeIngredients(): OfflineIngredient[] {
      return this.detail?.ingredients || []
    },
    activeSteps(): string[] {
      return this.detail?.steps || []
    },
    allergenText(): string {
      const allergens = this.selectedRecipe?.allergens || []
      return allergens.length ? allergens.join('、') : '未提供完整过敏原信息'
    },
  },
  onShow() {
    if (!this.recipes.length && !this.loading) void this.load()
  },
  methods: {
    load() {
      if (!session.householdId || this.loading) return
      this.loading = true
      this.error = ''
      return request<RecipePage>(`/households/${session.householdId}/recipes?limit=500`).then((page) => {
        this.recipes = page.items || []
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.loading = false })
    },
    selectRecipe(recipe: RecipeSummary) {
      this.selectedId = recipe.id
      this.detail = null
      this.detailLoading = true
      return request<RecipeDetail>(`/households/${session.householdId}/recipes/${recipe.id}`).then((detail) => {
        if (this.selectedId === recipe.id) this.detail = detail
      }, (error: Error) => { this.error = error.message })
        .finally(() => { this.detailLoading = false })
    },
    closeDetail() { this.selectedId = ''; this.detail = null },
    ingredientLabel(ingredient: OfflineIngredient): string { return ingredientLabel(ingredient) },
    recipeSymbol(recipe: RecipeSummary): string {
      const symbols: Record<string, string> = { 汤羹: '🍲', 主食: '🍚', 素菜: '🥬', 荤菜: '🥩', 水产: '🐟', 甜点: '🍰' }
      return symbols[recipe.category] || '🍲'
    },
    askAssistant() {
      const recipe = this.selectedRecipe
      const prompt = recipe
        ? `我想做${recipe.name}。请结合我家现有库存，告诉我是否够用，还缺什么，并给我具体做法。`
        : '请结合我家现有库存，帮我推荐今天吃什么，并告诉我具体怎么做。'
      setAssistantPrompt(prompt)
      this.closeDetail()
      uni.switchTab({ url: '/pages/assistant/index' })
    },
  },
})
</script>

<template>
  <view class="recipes-page">
    <SubpageHeader title="今天吃什么" />
    <view class="content">
      <text class="intro-title">从一顿好好吃饭开始</text>
      <text class="intro-copy">找一道喜欢的菜，把日常过得有滋有味。</text>
      <view class="library-notice">来自已审核菜谱库；页面展示不代表家庭库存或饮食适配结论，请核对食材和过敏原。</view>
      <input v-model="search" class="recipe-search" placeholder="搜索菜名或食材" confirm-type="search" />
      <view class="categories">
        <button v-for="item in categories" :key="item" :class="{ active: category === item }" @tap="category = item">{{ item }}</button>
      </view>
      <view class="list-heading"><text>菜谱灵感</text><text class="muted">{{ filteredRecipes.length }} 道</text></view>
      <text v-if="loading" class="loading">正在读取菜谱…</text>
      <view v-if="error" class="page-error"><text>{{ error }}</text><button @tap="load">重试</button></view>
      <button v-for="recipe in filteredRecipes" :key="recipe.id" class="recipe-card" @tap="selectRecipe(recipe)">
        <view class="recipe-art">{{ recipeSymbol(recipe) }}</view>
        <view class="summary">
          <text class="recipe-name">{{ recipe.name }}</text>
          <text class="recipe-meta">{{ recipe.duration_minutes }} 分钟 · {{ recipe.servings }} 人份</text>
          <text class="recipe-meta">{{ recipe.category }}{{ recipe.ingredient_count ? ` · ${recipe.ingredient_count} 种食材` : ' · 食材待补充' }}</text>
        </view>
        <text class="arrow">›</text>
      </button>
      <view v-if="!loading && !filteredRecipes.length && !error" class="empty-state">
        <text class="empty-title">没有找到匹配的菜谱</text>
        <text>试试其他菜名或食材。</text>
      </view>
      <button class="assistant-link" @tap="askAssistant">还没想好吃什么？　和助手聊聊 ›</button>
    </view>
    <view v-if="selectedRecipe" class="detail-mask" @tap="closeDetail">
      <view class="detail-sheet" role="dialog" aria-label="菜谱详情" @tap.stop>
        <view class="detail-heading">
          <text class="recipe-name">{{ selectedRecipe.name }}</text>
          <button class="close-detail" aria-label="关闭详情" @tap="closeDetail">×</button>
        </view>
        <scroll-view scroll-y class="detail-scroll">
          <text class="recipe-meta">{{ selectedRecipe.duration_minutes }} 分钟 · {{ selectedRecipe.servings }} 人份 · {{ selectedRecipe.category }}</text>
          <text v-if="detailLoading" class="loading">正在读取详情…</text>
          <text class="section-title">准备食材</text>
          <text v-for="ingredient in activeIngredients" :key="`${ingredient.name}-${ingredient.key || ''}`" class="ingredient">{{ ingredientLabel(ingredient) }}</text>
          <text v-if="!detailLoading && !activeIngredients.length" class="ingredient">食材信息待补充，无法据此判断过敏原。</text>
          <text class="allergy-note">过敏原提示：{{ allergenText }}。请核对全部食材与实际调料。</text>
          <text class="section-title">烹饪步骤</text>
          <view v-for="(step, index) in activeSteps" :key="step" class="step"><text class="step-number">{{ index + 1 }}</text><text>{{ step }}</text></view>
          <text v-if="!detailLoading && !activeSteps.length" class="ingredient">步骤信息待补充。</text>
          <text class="footnote">来源：{{ selectedRecipe.source_name || '知识库' }}。页面展示不等于饮食适配结论。</text>
          <button class="primary-button" @tap="askAssistant">向助手咨询搭配</button>
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.recipes-page { min-height: 100vh; background: #f3f7fc; }
.content { padding: 22px 16px 32px; }
.intro-title { display: block; color: #20344f; font-size: 23px; font-weight: 700; }
.intro-copy { display: block; margin-top: 8px; color: #718096; font-size: 13px; }
.library-notice { margin: 18px 0; padding: 10px 12px; border-radius: 10px; background: #fff7e6; color: #95651e; font-size: 12px; line-height: 1.6; }
.recipe-search { height: 48px; padding: 0 16px; border: 1px solid #e2e8f0; border-radius: 14px; background: #fff; font-size: 14px; }
.categories { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 24px; }
.categories button { margin: 0; padding: 0 14px; border-radius: 20px; line-height: 36px; font-size: 12px; background: #fff; color: #64748b; }
.categories .active { background: #3478f6; color: #fff; }
.list-heading { display: flex; justify-content: space-between; margin-bottom: 14px; font-size: 16px; font-weight: 600; color: #20344f; }
.muted { color: #7b8ba2; font-size: 12px; font-weight: 400; }
.recipe-card { display: flex; align-items: center; gap: 14px; width: 100%; margin: 0 0 12px; padding: 14px; background: #fff; border-radius: 16px; text-align: left; line-height: 1.5; font-weight: 400; }
.recipe-art { display: flex; align-items: center; justify-content: center; width: 76px; height: 84px; flex-shrink: 0; border-radius: 12px; background: #eef4ff; font-size: 40px; }
.summary { flex: 1; min-width: 0; }
.recipe-name { display: block; color: #20344f; font-size: 17px; font-weight: 600; }
.recipe-meta { display: block; margin-top: 10px; color: #6684ab; font-size: 12px; }
.arrow { color: #a3b2c7; font-size: 26px; }
.loading { display: block; padding: 20px 0; color: #7b8ba2; font-size: 13px; text-align: center; }
.page-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 12px 0; color: #b45f4e; font-size: 13px; }
.page-error button { margin: 0; padding: 0 12px; line-height: 32px; border-radius: 10px; background: #fff; color: #3478f6; font-size: 12px; }
.empty-state { padding: 28px 0; text-align: center; color: #7b8ba2; font-size: 13px; }
.empty-title { display: block; margin-bottom: 8px; color: #20344f; font-size: 16px; font-weight: 600; }
.assistant-link { margin: 20px 0 0; padding: 16px; border-radius: 14px; background: #e8f0ff; color: #3478f6; font-size: 13px; line-height: 1.6; }
.detail-mask { position: fixed; inset: 0; z-index: 100; display: flex; align-items: flex-end; background: #14243c66; }
.detail-sheet { display: flex; flex-direction: column; width: 100%; max-height: 85vh; padding: 20px 20px calc(20px + env(safe-area-inset-bottom)); box-sizing: border-box; overflow: hidden; border-radius: 24px 24px 0 0; background: #fff; }
.detail-heading { display: flex; align-items: center; justify-content: space-between; }
.detail-heading .recipe-name { flex: 1; min-width: 0; margin-right: 10px; overflow: hidden; font-size: 20px; text-overflow: ellipsis; white-space: nowrap; }
.close-detail { flex-shrink: 0; width: 40px; height: 40px; margin: 0; padding: 0; border-radius: 50%; background: #f2f6fb; color: #526780; font-size: 24px; line-height: 40px; }
/* 94px = 上下内边距 + 单行标题 + 标题与内容间距；限高后 scroll-view 才能在 panel 内滚动。 */
.detail-scroll { max-height: calc(85vh - 94px); max-height: calc(85vh - 94px - env(safe-area-inset-bottom)); margin-top: 14px; }
.section-title { display: block; margin: 20px 0 10px; color: #20344f; font-size: 15px; font-weight: 600; }
.ingredient { display: block; padding: 8px 0; border-bottom: 1px solid #f0f3f7; color: #526780; font-size: 14px; }
.allergy-note { display: block; margin-top: 12px; padding: 10px; border-radius: 10px; background: #fff7e6; color: #95651e; font-size: 12px; line-height: 1.6; }
.step { display: flex; gap: 10px; padding: 9px 0; color: #526780; font-size: 14px; line-height: 1.7; }
.step-number { display: flex; align-items: center; justify-content: center; width: 22px; height: 22px; flex-shrink: 0; border-radius: 50%; background: #e8f0ff; color: #3478f6; font-size: 11px; }
.footnote { display: block; margin: 16px 0; color: #8a98aa; font-size: 12px; line-height: 1.6; }
.primary-button { margin: 8px 0; border-radius: 12px; background: #3478f6; color: #fff; font-size: 14px; line-height: 44px; }
</style>