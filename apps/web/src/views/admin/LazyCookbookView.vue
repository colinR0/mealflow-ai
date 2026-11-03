<script lang="ts">
import { defineComponent } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown, Refresh, Search } from '@element-plus/icons-vue'

import RecipeCard from '../../components/RecipeCard.vue'
import RecipeDetailDrawer from '../../components/RecipeDetailDrawer.vue'
import { useAgentHandoffStore } from '../../stores/agentHandoff'
import {
  CATEGORY_ORDER,
  DIFFICULTY_OPTIONS,
  INGREDIENT_GROUPS,
  MATCH_MODES,
  SORT_OPTIONS,
  TOOL_OPTIONS,
  BIG_POT_HINT,
  filterCookbook,
  loadRecipes,
  matchByIngredients,
  type MatchMode,
  type Recipe,
  type SortKey,
} from '../../composables/recipeBook'

const PAGE_SIZE = 24
export default defineComponent({
  name: 'LazyCookbookView',
  components: { RecipeCard, RecipeDetailDrawer, ArrowDown, Refresh, Search },
  data() {
    return {
      PAGE_SIZE,
      Refresh,
      Search,
      recipes: [] as Recipe[],
      loading: true,
      errorMessage: '',
      activeTab: 'all' as 'all' | 'ingredients',
      ingredientsExpanded: true,
      keyword: '',
      category: '全部',
      difficulties: [] as string[],
      heatRange: [0, 9999] as [number, number],
      heatSliderBounds: [0, 9999] as [number, number],
      sort: 'default' as SortKey,
      page1: 1,
      openSections: { difficulty: true, heat: true },
      selectedIngredients: [] as string[],
      selectedTools: [] as string[],
      matchMode: 'fuzzy' as MatchMode,
      page2: 1,
      detailRecipe: null as Recipe | null,
      detailVisible: false,
      CATEGORY_ORDER,
      DIFFICULTY_OPTIONS,
      INGREDIENT_GROUPS,
      MATCH_MODES,
      SORT_OPTIONS,
      TOOL_OPTIONS,
      BIG_POT_HINT,
    }
  },
  computed: {
    categoryOptions() {
      const counts = new Map<string, number>()
      for (const recipe of this.recipes) {
        counts.set(recipe.category, (counts.get(recipe.category) ?? 0) + 1)
      }
      const ordered = CATEGORY_ORDER.filter((name) => counts.has(name)).map((name) => ({
        name,
        count: counts.get(name) ?? 0,
      }))
      for (const [name, count] of counts) {
        if (!CATEGORY_ORDER.includes(name)) ordered.push({ name, count })
      }
      return [{ name: '全部', count: this.recipes.length }, ...ordered]
    },
    heatIsFullRange(): boolean {
      return (
        this.heatRange[0] === this.heatSliderBounds[0] &&
        this.heatRange[1] === this.heatSliderBounds[1]
      )
    },
    tab1Filtered() {
      return filterCookbook(this.recipes, {
        keyword: this.keyword,
        category: this.category,
        difficulties: this.difficulties,
        heatRange: this.heatIsFullRange ? null : this.heatRange,
        sort: this.sort,
      })
    },
    tab1Paged(): Recipe[] {
      return this.tab1Filtered.slice((this.page1 - 1) * PAGE_SIZE, this.page1 * PAGE_SIZE)
    },
    tab2Matched(): Recipe[] {
      return matchByIngredients(this.recipes, this.selectedIngredients, this.matchMode)
    },
    tab2Paged(): Recipe[] {
      return this.tab2Matched.slice((this.page2 - 1) * PAGE_SIZE, this.page2 * PAGE_SIZE)
    },
  },
  watch: {
    keyword() {
      this.page1 = 1
    },
    category() {
      this.page1 = 1
    },
    difficulties() {
      this.page1 = 1
    },
    heatRange() {
      this.page1 = 1
    },
    sort() {
      this.page1 = 1
    },
    selectedIngredients() {
      this.page2 = 1
    },
    matchMode() {
      this.page2 = 1
    },
  },
  async mounted() {
    try {
      this.recipes = await loadRecipes()
      const heats = this.recipes
        .map((recipe) => recipe.heat)
        .filter((heat): heat is number => heat !== null)
      if (heats.length) {
        const low = Math.floor(Math.min(...heats) / 10) * 10
        const high = Math.ceil(Math.max(...heats) / 10) * 10
        this.heatSliderBounds = [low, high]
        this.heatRange = [low, high]
      }
    } catch (error) {
      this.errorMessage = error instanceof Error ? error.message : '菜谱数据加载失败'
    } finally {
      this.loading = false
    }
  },
  methods: {
    openRecipeDetail(recipe: Recipe) {
      this.detailRecipe = recipe
      this.detailVisible = true
    },
    resetTab1Filters() {
      this.keyword = ''
      this.category = '全部'
      this.difficulties = []
      this.heatRange = [...this.heatSliderBounds]
      this.sort = 'default'
    },
    clearTab2Selection() {
      this.selectedIngredients = []
      this.selectedTools = []
    },
    sendRecipeToAgent(recipe: Recipe) {
      const parts = [`我想做【${recipe.name}】（${recipe.category}，${recipe.difficulty}）。`]
      if (recipe.ingredients.length) {
        parts.push(`食材：${recipe.ingredients.join('、')}。`)
      }
      if (recipe.tools) {
        parts.push(`可用厨具：${recipe.tools}。`)
      }
      parts.push('请结合家庭成员的口味与忌口，给我一套能直接开火的定制做法。')
      useAgentHandoffStore().stage(parts.join(''))
      void this.$router.push('/admin/agent')
    },

    sendIngredientsToAgent() {
      if (!this.selectedIngredients.length) {
        ElMessage.warning('请先勾选家里现有的食材')
        return
      }
      const parts = [`家里现有食材：${this.selectedIngredients.join('、')}。`]
      if (this.selectedTools.length) {
        parts.push(`可用厨具：${this.selectedTools.join('、')}。`)
      }
      parts.push('请基于这些现有食材，推荐能做的菜。')
      useAgentHandoffStore().stage(parts.join(''))
      void this.$router.push('/admin/agent')
    },
  },
})
</script>

<template>
  <section class="cookbook-page">
    <header class="page-hero">
      <div>
        <span class="eyebrow">LAZY COOKBOOK</span>
        <h2>懒人宝典</h2>
        <p>原版菜谱视频库:筛选找菜、看原版视频,或一键把需求交给智能配餐 Agent 定制。</p>
      </div>
      <div class="hero-actions">
        <span class="data-note">共 {{ recipes.length }} 道菜谱</span>
      </div>
    </header>

    <div class="allergy-notice">
      ⚠️
      本页面展示原版菜谱，未自动适配家庭成员过敏原、忌口，如需个性化方案请点击按钮交给智能配餐Agent处理。
    </div>

    <nav class="cookbook-tabs">
      <button type="button" :class="{ active: activeTab === 'all' }" @click="activeTab = 'all'">
        菜谱大全
      </button>
      <button
        type="button"
        :class="{ active: activeTab === 'ingredients' }"
        @click="activeTab = 'ingredients'"
      >
        按食材找菜
      </button>
      <button
        v-if="activeTab === 'ingredients'"
        type="button"
        class="panel-toggle"
        :aria-expanded="ingredientsExpanded"
        @click="ingredientsExpanded = !ingredientsExpanded"
      >
        {{ ingredientsExpanded ? '收起筛选' : '展开食材筛选' }}
      </button>
    </nav>

    <div v-if="loading" class="state-block">菜谱加载中…</div>
    <div v-else-if="errorMessage" class="state-block error">{{ errorMessage }}</div>

    <div v-else class="cookbook-body">
      <!-- ===== Tab1 菜谱大全 ===== -->
      <div v-show="activeTab === 'all'" class="tab-layout">
        <aside class="filter-panel">
          <div class="filter-group">
            <div class="filter-title">菜谱分类</div>
            <div class="category-list">
              <button
                v-for="option in categoryOptions"
                :key="option.name"
                type="button"
                :class="{ active: category === option.name }"
                @click="category = option.name"
              >
                <span>{{ option.name }}</span>
                <small>{{ option.count }}</small>
              </button>
            </div>
          </div>

          <div class="filter-group">
            <button
              type="button"
              class="filter-title toggle"
              @click="openSections.difficulty = !openSections.difficulty"
            >
              难度筛选
              <el-icon :class="{ open: openSections.difficulty }"><arrow-down /></el-icon>
            </button>
            <div v-show="openSections.difficulty" class="filter-content">
              <el-checkbox-group v-model="difficulties" class="inline-checks">
                <el-checkbox
                  v-for="item in DIFFICULTY_OPTIONS"
                  :key="item"
                  :value="item"
                  border
                  size="small"
                >
                  {{ item }}
                </el-checkbox>
              </el-checkbox-group>
            </div>
          </div>

          <div class="filter-group">
            <button
              type="button"
              class="filter-title toggle"
              @click="openSections.heat = !openSections.heat"
            >
              热量区间
              <el-icon :class="{ open: openSections.heat }"><arrow-down /></el-icon>
            </button>
            <div v-show="openSections.heat" class="filter-content">
              <el-slider
                v-model="heatRange"
                range
                :min="heatSliderBounds[0]"
                :max="heatSliderBounds[1]"
                :step="10"
              />
              <p class="heat-value">
                {{ heatIsFullRange ? '热量不限' : `约 ${heatRange[0]} ~ ${heatRange[1]} kcal` }}
              </p>
            </div>
          </div>

          <div class="filter-group">
            <div class="filter-title">排序方式</div>
            <div class="filter-content">
              <el-select v-model="sort" size="small" aria-label="排序方式" style="width: 100%">
                <el-option
                  v-for="option in SORT_OPTIONS"
                  :key="option.key"
                  :label="option.label"
                  :value="option.key"
                />
              </el-select>
            </div>
          </div>

          <el-button class="reset-btn" :icon="Refresh" size="small" plain @click="resetTab1Filters">
            清空筛选
          </el-button>
        </aside>

        <div class="result-area result-area--with-toolbar">
          <div class="result-bar">
            <el-input
              v-model="keyword"
              class="search-input"
              :prefix-icon="Search"
              placeholder="按菜名搜索,如:番茄牛腩"
              clearable
            />
            <span class="result-count">共 {{ tab1Filtered.length }} 道</span>
          </div>
          <div class="result-scroll">
            <div v-if="tab1Paged.length" class="card-grid">
              <RecipeCard
                v-for="recipe in tab1Paged"
                :key="recipe.id"
                :recipe="recipe"
                @agent="sendRecipeToAgent"
                @open="openRecipeDetail"
              />
            </div>
            <div v-else class="state-block">🍳 没有找到匹配的菜谱,试试减少筛选条件或换个关键词</div>
          </div>
        </div>
        <div v-if="tab1Filtered.length > PAGE_SIZE" class="result-footer result-footer--external">
          <el-pagination
            v-model:current-page="page1"
            class="pager"
            background
            layout="prev, pager, next"
            :page-size="PAGE_SIZE"
            :total="tab1Filtered.length"
          />
        </div>
      </div>

      <!-- ===== Tab2 按食材找菜 ===== -->
      <div v-show="activeTab === 'ingredients'" class="tab2-layout">
        <div v-show="ingredientsExpanded" class="pick-panel">
          <div class="pick-section" v-for="group in INGREDIENT_GROUPS" :key="group.label">
            <div class="pick-title">{{ group.label }}</div>
            <el-checkbox-group v-model="selectedIngredients" class="inline-checks">
              <el-checkbox
                v-for="item in group.items"
                :key="item"
                :value="item"
                border
                size="small"
              >
                {{ item }}
              </el-checkbox>
            </el-checkbox-group>
          </div>

          <div class="pick-section">
            <div class="pick-title">厨具</div>
            <el-checkbox-group v-model="selectedTools" class="inline-checks">
              <el-checkbox
                v-for="tool in TOOL_OPTIONS"
                :key="tool"
                :value="tool"
                border
                size="small"
              >
                {{ tool }}
              </el-checkbox>
            </el-checkbox-group>
            <p class="pick-hint">
              提示:{{ BIG_POT_HINT }};厨具不参与本地筛选,仅随食材一并告诉 Agent。
            </p>
          </div>

          <div class="pick-section">
            <div class="pick-title">匹配模式</div>
            <div class="mode-cards">
              <button
                v-for="mode in MATCH_MODES"
                :key="mode.key"
                type="button"
                class="mode-card"
                :class="{ active: matchMode === mode.key }"
                @click="matchMode = mode.key"
              >
                <span class="mode-dot"></span>
                <strong>{{ mode.label }}</strong>
                <p>{{ mode.desc }}</p>
              </button>
            </div>
          </div>

          <div class="pick-actions">
            <div class="pick-summary">
              已选 <strong>{{ selectedIngredients.length }}</strong> 种食材
              <template v-if="selectedTools.length">、{{ selectedTools.length }} 件厨具</template>
              ,匹配到 <strong>{{ tab2Matched.length }}</strong> 道菜
            </div>
            <div class="pick-buttons">
              <el-button
                size="small"
                :disabled="!selectedIngredients.length && !selectedTools.length"
                @click="clearTab2Selection"
              >
                清空勾选
              </el-button>
              <el-button
                type="primary"
                :disabled="!selectedIngredients.length"
                @click="sendIngredientsToAgent"
              >
                将选中食材传给Agent
              </el-button>
            </div>
          </div>
        </div>

        <div class="result-area">
          <div class="result-scroll">
            <div v-if="tab2Paged.length" class="card-grid">
              <RecipeCard
                v-for="recipe in tab2Paged"
                :key="recipe.id"
                :recipe="recipe"
                @agent="sendRecipeToAgent"
                @open="openRecipeDetail"
              />
            </div>
            <div v-else class="state-block">🍳 没有匹配的菜谱,多勾几种食材或换成模糊匹配试试</div>
          </div>
        </div>
        <div v-if="tab2Matched.length > PAGE_SIZE" class="result-footer result-footer--external">
          <el-pagination
            v-model:current-page="page2"
            class="pager"
            background
            layout="prev, pager, next"
            :page-size="PAGE_SIZE"
            :total="tab2Matched.length"
          />
        </div>
      </div>
    </div>

    <RecipeDetailDrawer v-model="detailVisible" :recipe="detailRecipe" @agent="sendRecipeToAgent" />
  </section>
</template>

<style scoped>
/* 页面内 EP 组件主色统一改为系统深绿,不影响其他页面 */
.cookbook-page {
  --el-color-primary: var(--green);
  --el-color-primary-light-3: #2e6a61;
  --el-color-primary-light-5: #4d857c;
  --el-color-primary-light-7: #93b3ad;
  --el-color-primary-light-8: #b6ccc7;
  --el-color-primary-light-9: #e7f2ee;
  --el-color-primary-dark-2: #0c3d37;
  width: 100%;
  max-width: 1480px;
  height: calc(100vh - 118px);
  min-height: 0;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
  padding: 0;
  flex-shrink: 0;
}
.page-hero .eyebrow {
  display: inline-block;
}
.page-hero h2 {
  margin: 3px 0 4px;
  font-family: Georgia, 'Songti SC', serif;
  font-size: 34px;
  line-height: 1.1;
}
.page-hero p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}
.data-note {
  color: var(--muted);
  font-size: 13px;
  white-space: nowrap;
  padding-bottom: 3px;
}

.allergy-notice {
  flex-shrink: 0;
  margin-top: 8px;
  padding: 6px 10px;
  border: 1px solid #e0bd73;
  background: #fffaf0;
  border-radius: 10px;
  color: #a15b21;
  font-size: 12.5px;
  line-height: 1.6;
}

.cookbook-tabs {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  margin: 8px 0 0;
}
.cookbook-tabs button {
  min-width: 112px;
  padding: 7px 16px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: #fff;
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  transition: 0.16s ease;
}
.cookbook-tabs button.active {
  background: var(--green);
  border-color: var(--green);
  color: #fff;
}
.cookbook-tabs .panel-toggle {
  margin-left: auto;
  min-width: 0;
  padding: 7px 8px;
  border: 0;
  background: transparent;
  font-size: 12px;
  color: var(--green);
}

.cookbook-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding-top: 8px;
}

/* ===== Tab1:左侧筛选 + 右侧结果 ===== */
.tab-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) 36px;
  gap: 6px 10px;
  align-items: start;
  height: 100%;
  min-height: 0;
}
.filter-panel {
  grid-row: 1;
  grid-column: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-content: start;
  align-items: start;
  height: 100%;
  min-height: 0;
  gap: 4px;
  padding: 8px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 4px 18px rgba(33, 60, 54, 0.04);
  overflow: visible;
}
.filter-group {
  min-width: 0;
  padding-bottom: 6px;
  border-bottom: 1px solid #eef0ec;
}
.filter-group:last-of-type {
  padding-bottom: 0;
  border-bottom: 0;
}
.filter-title {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0;
  border: 0;
  background: none;
  font-size: 13px;
  font-weight: 700;
  color: var(--ink);
}
.filter-title .el-icon {
  transition: transform 0.18s ease;
  color: var(--muted);
}
.filter-title .el-icon.open {
  transform: rotate(180deg);
}
.filter-content {
  margin-top: 4px;
}

.category-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: 2px;
  margin-top: 4px;
}
.category-list button {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 8px;
  border: 0;
  border-radius: 8px;
  background: none;
  color: #3d524c;
  font-size: 13px;
  transition: 0.14s ease;
}
.category-list button:hover {
  background: #f1f5f1;
}
.category-list button.active {
  background: var(--green);
  color: #fff;
  font-weight: 650;
}
.category-list small {
  color: inherit;
  opacity: 0.62;
  font-variant-numeric: tabular-nums;
}

.inline-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.inline-checks :deep(.el-checkbox) {
  margin-right: 0;
  --el-checkbox-checked-bg-color: var(--green);
  --el-checkbox-checked-input-border-color: var(--green);
}
.inline-checks :deep(.el-checkbox.el-checkbox--border) {
  padding: 2px 6px;
  border-radius: 6px;
  height: 24px;
}
.inline-checks :deep(.el-checkbox__label) {
  font-size: 11px;
  padding-left: 4px;
}
.filter-panel .inline-checks {
  flex-wrap: nowrap;
}
.filter-panel :deep(.el-slider) {
  height: 24px;
  margin: 0 5px;
  width: calc(100% - 10px);
}

.heat-value {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 11px;
  text-align: center;
}

.sort-list {
  display: grid;
  gap: 4px;
}
.sort-list button {
  padding: 7px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: none;
  text-align: left;
  color: #3d524c;
  font-size: 13px;
  transition: 0.14s ease;
}
.sort-list button:hover {
  background: #f1f5f1;
}
.sort-list button.active {
  background: #e7f2ee;
  border-color: var(--green-2);
  color: var(--green);
  font-weight: 650;
}

.reset-btn {
  width: 100%;
  align-self: end;
}
.filter-panel .reset-btn {
  min-height: 28px;
  height: 28px;
}

/* ===== 结果区 ===== */
.result-area {
  grid-row: 1;
  grid-column: 2;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
.result-area--with-toolbar {
  grid-template-rows: auto minmax(0, 1fr);
}
.result-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 0 0 6px;
  background: #f6f5ef;
  border-bottom: 1px solid #e4e7e2;
}
.result-scroll {
  min-height: 0;
  overflow-y: auto;
  padding: 8px 4px 4px 0;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.result-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  padding-top: 0;
  background: #f6f5ef;
  border-top: 1px solid #e4e7e2;
}
.result-footer--external {
  grid-column: 2;
  grid-row: 2;
  justify-content: flex-end;
  padding: 0;
  background: transparent;
  border-top: 0;
}
.search-input {
  width: min(360px, 58%);
}
.result-count {
  color: var(--muted);
  font-size: 13px;
}
.result-bar--summary {
  justify-content: space-between;
}
.result-heading {
  color: var(--ink);
  font-size: 14px;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 8px;
  align-items: stretch;
}
.card-grid :deep(.recipe-card) {
  padding: 9px;
  gap: 4px;
}
.card-grid :deep(.card-topline h3) {
  font-size: 14px;
}
.card-grid :deep(.card-ingredients) {
  font-size: 12px;
  line-height: 1.5;
}
.card-grid :deep(.card-actions) {
  gap: 4px;
  padding-top: 0;
}
.card-grid :deep(.card-actions .el-button) {
  margin-left: 0;
  min-height: 28px;
  height: 28px;
  font-size: 11px;
  padding: 0 6px;
}
.pager {
  justify-content: flex-end;
}

.state-block {
  padding: 46px 20px;
  border: 1px dashed #bfc8c3;
  border-radius: 14px;
  color: var(--muted);
  text-align: center;
  font-size: 13.5px;
}
.state-block.error {
  color: #a43c36;
  border-color: #dfb2ae;
}

/* ===== Tab2:食材/厨具/模式面板 ===== */
.tab2-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: auto minmax(0, 1fr) auto;
  row-gap: 6px;
  align-items: start;
  height: 100%;
  min-height: 0;
}
.pick-panel {
  grid-row: 1;
  grid-column: 1;
  display: grid;
  grid-template-columns: 1.5fr 1fr 1fr;
  align-items: start;
  gap: 8px 12px;
  height: 210px;
  padding: 10px 12px;
  overflow: visible;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 4px 18px rgba(33, 60, 54, 0.04);
}
.tab2-layout > .result-area {
  grid-column: 1;
  grid-row: 2;
}
.tab2-layout > .result-footer {
  grid-column: 1;
  grid-row: 3;
}
.pick-section {
  min-width: 0;
}
.pick-section + .pick-section {
  padding-top: 0;
  border-top: 0;
}
.pick-title {
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 4px;
}
.pick-hint {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.3;
}

.mode-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 4px;
}
.mode-card {
  position: relative;
  display: grid;
  gap: 3px;
  padding: 5px 5px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: #fafaf6;
  text-align: left;
  transition: 0.16s ease;
}
.mode-card:hover {
  border-color: var(--green-2);
}
.mode-card.active {
  border-color: var(--green);
  background: #f0f7f3;
  box-shadow: 0 6px 16px rgba(15, 76, 69, 0.08);
}
.mode-card strong {
  font-size: 11px;
  color: var(--ink);
  padding-right: 12px;
}
.mode-card.active strong {
  color: var(--green);
}
.mode-card p {
  margin: 0;
  color: var(--muted);
  font-size: 10.5px;
  line-height: 1.3;
}
.mode-dot {
  position: absolute;
  top: 6px;
  right: 4px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 1.5px solid #c3ccc7;
  background: #fff;
  transition: 0.16s ease;
}
.mode-card.active .mode-dot {
  border-color: var(--green);
  border-width: 4px;
}

.pick-actions {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  padding-top: 0;
}
.pick-summary {
  color: var(--muted);
  font-size: 11px;
}
.pick-summary strong {
  color: var(--green);
  font-size: 15px;
}
.pick-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pick-buttons :deep(.el-button) {
  flex: 1;
  margin-left: 0;
  height: 28px;
  min-height: 28px;
  font-size: 11px;
  padding: 0 6px;
}
.pick-buttons :deep(.el-button--primary) {
  flex: 2;
}

.filter-panel,
.pick-panel,
.result-scroll {
  scrollbar-width: thin;
  scrollbar-color: #9ab1ab transparent;
  overscroll-behavior: contain;
}
.filter-panel::-webkit-scrollbar,
.pick-panel::-webkit-scrollbar,
.result-scroll::-webkit-scrollbar {
  width: 7px;
}
.filter-panel::-webkit-scrollbar-thumb,
.pick-panel::-webkit-scrollbar-thumb,
.result-scroll::-webkit-scrollbar-thumb {
  background: #9ab1ab;
  border-radius: 999px;
}

@media (max-width: 1300px) {
  .cookbook-page {
    max-width: none;
  }
  .card-grid {
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  }
}

@media (max-width: 760px) {
  .tab-layout,
  .tab2-layout {
    grid-template-columns: 1fr;
  }
  .cookbook-body {
    overflow-y: auto;
  }
  .tab-layout,
  .tab2-layout {
    height: auto;
  }
  .filter-panel,
  .pick-panel,
  .result-area {
    grid-row: auto;
    grid-column: 1;
  }
  .filter-panel,
  .pick-panel {
    grid-template-columns: 1fr;
    max-height: none;
    height: auto;
    overflow: visible;
  }
  .result-area,
  .result-area--with-toolbar {
    display: flex;
    flex-direction: column;
    height: auto;
    overflow: visible;
  }
  .result-scroll {
    overflow: visible;
    padding-right: 0;
  }
  .result-bar,
  .result-footer {
    background: transparent;
  }
  .result-footer--external {
    grid-column: 1;
    grid-row: auto;
    justify-content: center;
  }
  .page-hero {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }
  .data-note {
    padding-bottom: 0;
  }
  .search-input {
    width: min(100%, 420px);
  }
  .mode-cards {
    grid-template-columns: 1fr;
  }
  .pick-actions {
    flex-direction: column;
    align-items: stretch;
  }
  .pick-buttons {
    display: flex;
  }
  .pick-buttons :deep(.el-button) {
    width: auto;
  }
}
</style>
