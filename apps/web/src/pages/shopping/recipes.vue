<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'

// Examples are separate from household stock and are not dietary suitability claims.
const RECIPES = [
  { id: 'tomato-egg', name: '番茄炒蛋', symbol: '🍅', category: '快手家常', minutes: 15, servings: 2, ingredients: ['番茄 2 个', '鸡蛋 3 个', '食用油、盐适量'], allergens: '含鸡蛋', steps: ['番茄洗净切块，鸡蛋打散。', '锅中热油，倒入蛋液炒至凝固后盛出。', '炒软番茄，加入鸡蛋和盐，翻炒均匀。'], color: '#fff0e9' },
  { id: 'broccoli', name: '蒜蓉西兰花', symbol: '🥦', category: '蔬菜小炒', minutes: 12, servings: 2, ingredients: ['西兰花 1 颗', '大蒜 3 瓣', '食用油、盐适量'], allergens: '未提供完整过敏原信息', steps: ['西兰花切小朵洗净，大蒜切末。', '西兰花焯水后沥干。', '炒香蒜末，加入西兰花和盐翻炒至熟。'], color: '#eaf5e8' },
  { id: 'corn-soup', name: '玉米排骨汤', symbol: '🌽', category: '暖心汤羹', minutes: 60, servings: 3, ingredients: ['排骨 400 克', '玉米 1 根', '生姜、盐适量'], allergens: '未提供完整过敏原信息', steps: ['排骨洗净焯水，玉米切段。', '将排骨、玉米、姜片放入锅中，加水。', '煮开后转小火炖至排骨熟软，最后调味。'], color: '#fff6dd' },
]

export default defineComponent({
  name: 'RecipeBrowsePage',
  components: { SubpageHeader },
  data() { return { search: '', category: '全部', categories: ['全部', '快手家常', '蔬菜小炒', '暖心汤羹'], selectedId: '' } },
  computed: {
    recipes() { return RECIPES.filter((recipe) => (this.category === '全部' || recipe.category === this.category) && `${recipe.name}${recipe.ingredients.join('')}`.includes(this.search.trim())) },
    selectedRecipe() { return RECIPES.find((recipe) => recipe.id === this.selectedId) },
  },
  methods: { askAssistant() { uni.switchTab({ url: '/pages/assistant/index' }) } },
})
</script>

<template>
  <view class="recipes-page">
    <SubpageHeader title="今天吃什么" />
    <view class="content">
      <text class="intro-title">从一顿好好吃饭开始</text><text class="intro-copy">找一道喜欢的菜，把日常过得有滋有味。</text>
      <view class="example-notice">页面示例 · 尚未接入食谱检索，不代表家庭库存或饮食适配结果</view>
      <input v-model="search" class="recipe-search" placeholder="搜索菜名或食材" confirm-type="search" />
      <view class="categories"><button v-for="item in categories" :key="item" :class="{ active: category === item }" @tap="category = item">{{ item }}</button></view>
      <view class="list-heading"><text>菜谱灵感</text><text class="muted">{{ recipes.length }} 道示例</text></view>
      <button v-for="recipe in recipes" :key="recipe.id" class="recipe-card" @tap="selectedId = recipe.id"><view class="recipe-art" :style="{ background: recipe.color }">{{ recipe.symbol }}</view><view class="summary"><text class="recipe-name">{{ recipe.name }}</text><text class="recipe-meta">{{ recipe.minutes }} 分钟 · {{ recipe.servings }} 人份</text><text class="recipe-meta">{{ recipe.category }}</text></view><text class="arrow">›</text></button>
      <view v-if="!recipes.length" class="empty-state"><text class="empty-title">没有找到匹配的示例</text><text>试试其他食材，或切换分类。</text></view>
      <button class="assistant-link" @tap="askAssistant">还没想好吃什么？　和助手聊聊 ›</button>
    </view>
    <view v-if="selectedRecipe" class="detail-mask" @tap="selectedId = ''">
      <view class="detail-sheet" role="dialog" aria-label="菜谱详情" @tap.stop>
        <view class="detail-heading"><text class="recipe-name">{{ selectedRecipe.name }}</text><button class="close-detail" aria-label="关闭详情" @tap="selectedId = ''">×</button></view>
        <scroll-view scroll-y class="detail-scroll">
          <text class="recipe-meta">{{ selectedRecipe.minutes }} 分钟 · {{ selectedRecipe.servings }} 人份 · 示例菜谱</text>
          <text class="section-title">准备食材</text><text v-for="ingredient in selectedRecipe.ingredients" :key="ingredient" class="ingredient">{{ ingredient }}</text>
          <text class="allergy-note">过敏原提示：{{ selectedRecipe.allergens }}。请核对全部食材与实际调料。</text>
          <text class="section-title">烹饪步骤</text><view v-for="(step, index) in selectedRecipe.steps" :key="step" class="step"><text class="step-number">{{ index + 1 }}</text><text>{{ step }}</text></view>
          <text class="footnote">示例尚未关联家庭档案、库存或采购清单。</text>
        </scroll-view>
        <button class="primary-button" @tap="askAssistant">向助手咨询搭配</button>
      </view>
    </view>
  </view>
</template>

<style scoped lang="scss">
.recipes-page { min-height: 100vh; background: #f3f7fc; }.content { padding: 22px 16px 32px; }.intro-title { display: block; color: #20344f; font-size: 23px; font-weight: 700; }.intro-copy { display: block; margin-top: 8px; color: #718096; font-size: 13px; }.example-notice { margin: 18px 0; padding: 10px 12px; border-radius: 10px; background: #fff7e6; color: #95651e; font-size: 12px; line-height: 1.6; }.recipe-search { height: 48px; padding: 0 16px; border: 1px solid #e2e8f0; border-radius: 14px; background: #fff; font-size: 14px; }.categories { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 24px; }.categories button { margin: 0; padding: 0 14px; border-radius: 20px; line-height: 36px; font-size: 12px; background: #fff; color: #64748b; }.categories .active { background: #3478f6; color: #fff; }.list-heading { display: flex; justify-content: space-between; margin-bottom: 14px; font-size: 16px; font-weight: 600; color: #20344f; }.muted { color: #7b8ba2; font-size: 12px; font-weight: 400; }.recipe-card { display: flex; align-items: center; gap: 14px; width: 100%; margin: 0 0 12px; padding: 14px; background: #fff; border-radius: 16px; text-align: left; line-height: 1.5; font-weight: 400; }.recipe-art { display: flex; align-items: center; justify-content: center; width: 76px; height: 84px; flex-shrink: 0; border-radius: 12px; font-size: 40px; }.summary { flex: 1; min-width: 0; }.recipe-name { display: block; color: #20344f; font-size: 17px; font-weight: 600; }.recipe-meta { display: block; margin-top: 10px; color: #6684ab; font-size: 12px; }.arrow { color: #a3b2c7; font-size: 26px; }.assistant-link { margin: 20px 0 0; padding: 16px; border-radius: 14px; background: #e8f0ff; color: #3478f6; font-size: 13px; line-height: 1.6; }.detail-mask { position: fixed; inset: 0; z-index: 100; display: flex; align-items: flex-end; background: #14243c66; }.detail-sheet { display: flex; flex-direction: column; width: 100%; max-height: 85vh; padding: 20px 20px calc(20px + env(safe-area-inset-bottom)); border-radius: 24px 24px 0 0; background: #fff; }.detail-heading { display: flex; align-items: center; justify-content: space-between; }.close-detail { margin: 0; padding: 0; width: 40px; height: 40px; line-height: 40px; background: #f1f5f9; color: #64748b; border-radius: 50%; font-size: 24px; }.detail-scroll { flex: 1; min-height: 0; max-height: 58vh; }.section-title { display: block; margin: 24px 0 12px; font-size: 16px; font-weight: 600; }.ingredient { display: block; padding: 10px 0; border-bottom: 1px solid #edf2f7; color: #52657c; font-size: 14px; }.allergy-note { display: block; margin-top: 16px; padding: 12px; border-radius: 10px; background: #fff4ed; color: #9a5c31; font-size: 12px; line-height: 1.7; }.step { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 16px; color: #52657c; font-size: 14px; line-height: 1.8; }.step-number { padding: 0 8px; border-radius: 7px; background: #edf4ff; color: #3478f6; }
</style>
