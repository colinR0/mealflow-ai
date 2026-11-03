<script lang="ts">
import { defineComponent } from 'vue'
import { MagicStick, VideoPlay } from '@element-plus/icons-vue'
import type { Recipe } from '../types/recipe'

export default defineComponent({
  name: 'RecipeCard',
  props: { recipe: { type: Object as () => Recipe, required: true } },
  emits: ['agent', 'open'],
  data() {
    return { imageFailed: false, MagicStick, VideoPlay }
  },
  computed: {
    difficultyClass(): string {
      return (
        ({ 简单: 'easy', 中等: 'medium', 困难: 'hard' } as Record<string, string>)[
          this.recipe.difficulty
        ] ?? 'easy'
      )
    },
    howtoLabel(): string {
      if (this.recipe.steps.length) return `📄 图文 ${this.recipe.steps.length} 步`
      if (this.recipe.videoUrl) return '▶ 视频教程'
      return ''
    },
    showImage(): boolean {
      return this.recipe.image !== null && !this.imageFailed
    },
  },
  watch: {
    'recipe.id'() {
      this.imageFailed = false
    },
  },
  methods: {
    openVideo() {
      if (this.recipe.videoUrl) window.open(this.recipe.videoUrl, '_blank', 'noopener')
    },
  },
})
</script>

<template>
  <article class="recipe-card" @click="$emit('open', recipe)">
    <div class="card-media">
      <img
        v-if="showImage"
        :src="recipe.image!"
        :alt="recipe.name"
        loading="lazy"
        referrerpolicy="no-referrer"
        @error="imageFailed = true"
      />
      <div v-else class="media-fallback" aria-hidden="true">🍳</div>
    </div>
    <div class="card-topline">
      <h3 :title="recipe.name">{{ recipe.name }}</h3>
      <span class="category-chip">{{ recipe.category }}</span>
    </div>
    <div class="card-meta">
      <span class="difficulty-chip" :class="difficultyClass">{{ recipe.difficulty }}</span>
      <span v-if="recipe.heat !== null" class="heat-chip">{{ recipe.heat }} kcal</span>
      <span class="pop-chip">👍 {{ recipe.likes }} · ☆ {{ recipe.collect }}</span>
    </div>
    <div v-if="howtoLabel" class="howto-chip">{{ howtoLabel }}</div>
    <p class="card-ingredients" :title="recipe.ingredients.join('、')">
      <small>食材</small>
      <span>{{ recipe.ingredients.join('、') || '—' }}</span>
    </p>
    <div v-if="recipe.tags.length" class="card-tags">
      <span v-for="tag in recipe.tags" :key="tag">{{ tag }}</span>
    </div>
    <div class="card-actions">
      <el-button
        size="small"
        :disabled="!recipe.videoUrl"
        :icon="VideoPlay"
        @click.stop="openVideo"
      >
        查看原版视频
      </el-button>
      <el-button
        size="small"
        type="primary"
        :icon="MagicStick"
        @click.stop="$emit('agent', recipe)"
      >
        交给Agent生成定制菜谱
      </el-button>
    </div>
  </article>
</template>

<style scoped>
.recipe-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 4px 18px rgba(33, 60, 54, 0.04);
  transition:
    transform 0.18s ease,
    box-shadow 0.18s ease,
    border-color 0.18s ease;
  cursor: pointer;
}
.recipe-card:hover {
  transform: translateY(-2px);
  border-color: var(--green-2);
  box-shadow: 0 12px 30px rgba(15, 76, 69, 0.1);
}
.card-media {
  margin: -16px -16px 0;
  height: 148px;
  overflow: hidden;
  border-radius: 14px 14px 0 0;
  background: #f1f3ef;
}
.card-media img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.media-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 34px;
  background: linear-gradient(135deg, #e7f2ee 0%, #f6f4ec 60%, #fdf3e0 100%);
}
.card-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.card-topline h3 {
  margin: 0;
  font-size: 15px;
  line-height: 1.45;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.category-chip {
  flex-shrink: 0;
  padding: 3px 9px;
  border-radius: 999px;
  background: #e7f2ee;
  color: var(--green);
  font-size: 11px;
  font-weight: 600;
}
.card-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.difficulty-chip {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 650;
}
.difficulty-chip.easy {
  background: #e7f2ee;
  color: #0f6b4f;
}
.difficulty-chip.medium {
  background: #fdf3e0;
  color: #a2701d;
}
.difficulty-chip.hard {
  background: #fdece7;
  color: #a4442c;
}
.heat-chip {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.pop-chip {
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.howto-chip {
  align-self: flex-start;
  padding: 2px 8px;
  border-radius: 6px;
  background: #e7f2ee;
  color: #0f6b4f;
  font-size: 11px;
  font-weight: 600;
}
.card-ingredients {
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 12.5px;
  line-height: 1.7;
  color: #3d524c;
}
.card-ingredients small {
  flex-shrink: 0;
  margin-right: 7px;
  padding: 1px 6px;
  border-radius: 5px;
  background: #f1f3ef;
  color: var(--muted);
  font-size: 11px;
}
.card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.card-tags span {
  padding: 2px 8px;
  border-radius: 6px;
  background: #f1f3ef;
  color: var(--muted);
  font-size: 11px;
}
.card-actions {
  margin-top: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-top: 4px;
}
.card-actions .el-button {
  flex: 1;
}
</style>
