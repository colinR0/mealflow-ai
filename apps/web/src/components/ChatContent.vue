<script lang="ts">
import { defineComponent } from 'vue'

function escapeHtml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function inline(value: string): string {
  return escapeHtml(value).replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

export default defineComponent({
  name: 'ChatContent',
  props: { text: { type: String, required: true } },
  computed: {
    blocks(): { code: boolean; text: string; html: string }[] {
      // Render a small Markdown subset; raw HTML and links never become executable markup.
      return this.text.split(/```[^\n]*\n([\s\S]*?)(?:```|$)/g).map((text, index) => ({
        code: index % 2 === 1,
        text,
        html: text.split('\n').map((line) => {
          if (/^#{1,6}\s/.test(line)) return `<p><strong>${inline(line.replace(/^#{1,6}\s+/, ''))}</strong></p>`
          if (/^\s*[-*]\s/.test(line)) return `<p>• ${inline(line.replace(/^\s*[-*]\s+/, ''))}</p>`
          return `<p>${inline(line) || '<br>'}</p>`
        }).join(''),
      }))
    },
  },
})
</script>

<template>
  <view class="chat-content">
    <template v-for="(block, index) in blocks" :key="index">
      <text v-if="block.code" class="code-block" selectable>{{ block.text }}</text>
      <rich-text v-else :nodes="block.html" selectable />
    </template>
  </view>
</template>

<style scoped>
.chat-content { color: #263449; font-size: 28rpx; line-height: 1.85; overflow-wrap: anywhere; }
.code-block { display: block; margin: 18rpx 0; padding: 22rpx; border-radius: 14rpx; background: #eef2f7; color: #334155; font-family: monospace; white-space: pre-wrap; font-size: 25rpx; }
</style>
