<script lang="ts">
import { defineComponent } from 'vue'
import { MagicStick, VideoPlay } from '@element-plus/icons-vue'
import type { Recipe } from '../types/recipe'

export default defineComponent({
  name: 'RecipeDetailDrawer',
  props: {
    recipe: { type: Object as () => Recipe | null, default: null },
    modelValue: { type: Boolean, default: false },
  },
  emits: ['update:modelValue', 'agent'],
  data() {
    return { MagicStick, VideoPlay }
  },
  computed: {
    visible: {
      get(): boolean {
        return this.modelValue
      },
      set(value: boolean) {
        this.$emit('update:modelValue', value)
      },
    },
    playerSrc(): string {
      return this.recipe?.bvid
        ? `https://player.bilibili.com/player.html?bvid=${this.recipe.bvid}&autoplay=0&high_quality=1`
        : ''
    },
    difficultyClass(): string {
      return (
        ({ 简单: 'easy', 中等: 'medium', 困难: 'hard' } as Record<string, string>)[
          this.recipe?.difficulty ?? ''
        ] ?? 'easy'
      )
    },
  },
  methods: {
    openVideo() {
      if (this.recipe?.videoUrl) window.open(this.recipe.videoUrl, '_blank', 'noopener')
    },
  },
})
</script>

<template>
  <el-drawer
    v-model="visible"
    size="680px"
    class="recipe-detail-drawer"
    destroy-on-close
    :with-header="true"
  >
    <template #header>
      <div class="drawer-title">
        <span>LAZY COOKBOOK</span>
        <h2>{{ recipe?.name ?? '' }}</h2>
      </div>
    </template>

    <div v-if="recipe" class="detail-body">
      <div class="detail-media">
        <img
          v-if="recipe.image"
          :src="recipe.image"
          :alt="recipe.name"
          referrerpolicy="no-referrer"
          @error="($event.target as HTMLImageElement).style.display = 'none'"
        />
        <div v-else class="media-fallback" aria-hidden="true">🍳</div>
      </div>

      <div class="detail-meta">
        <span class="category-chip">{{ recipe.category }}</span>
        <span class="difficulty-chip" :class="difficultyClass">{{ recipe.difficulty }}</span>
        <span v-if="recipe.heat !== null" class="heat-chip">{{ recipe.heat }} kcal</span>
        <span class="pop-chip">👍 {{ recipe.likes }} · ☆ {{ recipe.collect }}</span>
      </div>

      <section class="detail-section">
        <h3>食材</h3>
        <div class="chip-row">
          <span v-for="item in recipe.ingredients" :key="item" class="ingredient-chip">{{
            item
          }}</span>
          <span v-if="!recipe.ingredients.length" class="empty-note">—</span>
        </div>
        <div v-if="recipe.tools.length" class="tools-row">
          <small>厨具</small>
          <span>{{ recipe.tools.join('、') }}</span>
        </div>
      </section>

      <section class="detail-section">
        <h3>做法</h3>
        <ol v-if="recipe.steps.length" class="steps-timeline">
          <li v-for="(step, index) in recipe.steps" :key="index">
            <span class="step-index">{{ index + 1 }}</span>
            <p>{{ step }}</p>
          </li>
        </ol>
        <div v-else-if="playerSrc" class="video-wrap">
          <p class="video-note">这道菜的做法以原版视频提供,直接观看:</p>
          <iframe
            :src="playerSrc"
            scrolling="no"
            frameborder="no"
            allowfullscreen="true"
            referrerpolicy="no-referrer"
          ></iframe>
        </div>
        <div v-else class="video-wrap">
          <p class="video-note">这道菜的做法以原版视频提供:</p>
          <el-button type="primary" :icon="VideoPlay" @click="openVideo">去 B 站观看做法</el-button>
        </div>
        <div v-if="recipe.videoUrl" class="video-link">
          <el-button size="small" text type="primary" @click="openVideo">
            在 B 站打开完整视频 ↗
          </el-button>
        </div>
      </section>
    </div>

    <template #footer>
      <div class="drawer-footer">
        <el-button :disabled="!recipe?.videoUrl" :icon="VideoPlay" @click="openVideo">
          查看原版视频
        </el-button>
        <el-button v-if="recipe" type="primary" :icon="MagicStick" @click="$emit('agent', recipe)">
          ✦ 交给Agent生成定制菜谱
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.drawer-title span {
  font-size: 11px;
  letter-spacing: 2px;
  color: var(--muted);
}
.drawer-title h2 {
  margin: 0;
  font-size: 20px;
  color: #21403a;
}
.detail-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.detail-media {
  height: 240px;
  border-radius: 14px;
  overflow: hidden;
  background: #f1f3ef;
  flex-shrink: 0;
}
.detail-media img {
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
  font-size: 52px;
  background: linear-gradient(135deg, #e7f2ee 0%, #f6f4ec 60%, #fdf3e0 100%);
}
.detail-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.category-chip {
  padding: 3px 10px;
  border-radius: 999px;
  background: #e7f2ee;
  color: var(--green);
  font-size: 12px;
  font-weight: 600;
}
.difficulty-chip {
  padding: 2px 9px;
  border-radius: 6px;
  font-size: 12px;
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
.heat-chip,
.pop-chip {
  color: var(--muted);
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}
.detail-section h3 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #21403a;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ingredient-chip {
  padding: 3px 10px;
  border-radius: 999px;
  background: #f1f3ef;
  color: #3d524c;
  font-size: 12.5px;
}
.empty-note {
  color: var(--muted);
  font-size: 12.5px;
}
.tools-row {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: #3d524c;
}
.tools-row small {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: 5px;
  background: #f1f3ef;
  color: var(--muted);
  font-size: 11px;
}
.steps-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.steps-timeline li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.step-index {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: var(--green);
  color: #fff;
  font-size: 12px;
  font-weight: 650;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.steps-timeline p {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.75;
  color: #3d524c;
}
.video-wrap iframe {
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 0;
  border-radius: 12px;
  background: #000;
  display: block;
}
.video-note {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--muted);
}
.video-link {
  margin-top: 10px;
}
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
