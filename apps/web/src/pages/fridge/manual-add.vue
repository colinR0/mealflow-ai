<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'
import { ApiError, request } from '../../shared/transport'
import { session } from '../../shared/session'
import type { IngredientDictItem, InventoryLot } from '../../shared/types'

export default defineComponent({
  name: 'FridgeManualAddPage',
  components: { SubpageHeader, BottomNav },
  data() {
    return {
      session,
      name: '',
      selected: null as IngredientDictItem | null,
      suggestions: [] as IngredientDictItem[],
      quantityStatus: 'unknown' as 'known' | 'unknown',
      quantity: '',
      unit: '',
      note: '',
      error: '',
      busy: false,
      saved: false,
      searchTimer: undefined as ReturnType<typeof setTimeout> | undefined,
    }
  },
  computed: {
    householdLabel(): string {
      return this.session.households.find((item) => item.id === this.session.householdId)?.name || '当前家庭'
    },
    canSubmit(): boolean {
      return Boolean(this.name.trim() || this.selected)
        && (this.quantityStatus === 'unknown' || (
          Boolean(this.quantity.trim())
          && Number.isFinite(Number(this.quantity))
          && Number(this.quantity) >= 0
          && Boolean(this.unit.trim())
        ))
    },
  },
  onUnload() { clearTimeout(this.searchTimer) },
  watch: {
    name(value: string) {
      if (this.selected && this.selected.name !== value.trim()) this.selected = null
      clearTimeout(this.searchTimer)
      this.searchTimer = setTimeout(() => void this.searchDictionary(value), 200)
    },
  },
  methods: {
    selectQuantityStatus(value: 'known' | 'unknown') {
      this.quantityStatus = value
      if (value === 'unknown') this.quantity = ''
    },
    async searchDictionary(term: string) {
      const query = term.trim()
      if (!query) { this.suggestions = []; return }
      try {
        this.suggestions = await request<IngredientDictItem[]>(`/ingredients?search=${encodeURIComponent(query)}&limit=8`)
      } catch {
        this.suggestions = []
      }
    },
    pickIngredient(item: IngredientDictItem) {
      this.selected = item
      this.name = item.name
      this.suggestions = []
      if (!this.unit.trim()) this.unit = item.default_unit
    },
    async submit() {
      if (this.busy || this.saved) return
      this.error = ''
      if (!this.name.trim() && !this.selected) { this.error = '请填写或选择食材'; return }
      if (!this.canSubmit) { this.error = '请填写有效数量，或选择数量未知'; return }
      if (!session.householdId) { this.error = '尚未选择家庭'; return }
      const householdId = session.householdId
      const payload = {
        name: this.name.trim() || this.selected?.name,
        ingredient_id: this.selected?.id,
        quantity_status: this.quantityStatus,
        quantity: this.quantityStatus === 'known' ? this.quantity : null,
        unit: this.quantityStatus === 'known' ? this.unit.trim() : null,
        notes: this.note.trim(),
      }
      this.busy = true
      try {
        const result = await request<InventoryLot & { outcome?: string }>(`/households/${householdId}/inventory-lots`, payload)
        this.saved = true
        const title = result.outcome === 'created' ? '已新增' : result.outcome === 'restored' ? '已恢复在库' : '已更新库存'
        uni.showToast({ title, icon: 'success' })
        setTimeout(() => { if (householdId === session.householdId) uni.navigateBack({ delta: 1 }) }, 400)
      } catch (error) {
        if (error instanceof ApiError && error.statusCode === 409) {
          this.error = '记录已变化，请返回列表刷新后重试'
        } else {
          this.error = error instanceof Error ? error.message : '保存失败'
        }
      } finally { this.busy = false }
    },
    cancel() { if (!this.busy) uni.navigateBack({ delta: 1 }) },
  },
})
</script>

<template>
  <view class="manual-page">
    <SubpageHeader title="手动新增库存" :switchable="false" />
    <view class="target-banner">
      <text class="target-label">家庭 · {{ householdLabel }}</text>
      <text>同名或别名再次登记会覆盖数量，不会累加；已用完/已移除的食材会恢复为在库。</text>
    </view>
    <scroll-view scroll-y class="manual-scroll">
      <view class="form-panel">
        <text class="panel-title">食材信息</text>
        <view class="form-section">
          <text class="field-label">食材名称</text>
          <input v-model="name" maxlength="120" placeholder="搜索字典或直接输入" />
          <view v-if="suggestions.length" class="suggest-list">
            <view v-for="item in suggestions" :key="item.id" class="suggest-item" @tap="pickIngredient(item)">
              <text class="suggest-name">{{ item.name }}</text>
              <text class="suggest-meta">{{ item.category }} · 默认 {{ item.default_unit }}</text>
            </view>
          </view>
          <text v-if="selected" class="selected-hint">已选择字典食材，保存时使用稳定 ingredient_id</text>
        </view>
        <view class="form-section">
          <text class="field-label">数量状态</text>
          <view class="segmented">
            <text :class="{ selected: quantityStatus === 'known' }" @tap="selectQuantityStatus('known')">已知数量</text>
            <text :class="{ selected: quantityStatus === 'unknown' }" @tap="selectQuantityStatus('unknown')">数量未知</text>
          </view>
        </view>
        <view v-if="quantityStatus === 'known'" class="form-row">
          <text>实际数量</text>
          <view class="quantity-field">
            <input v-model="quantity" type="digit" placeholder="非负数量" />
            <input v-model="unit" maxlength="20" placeholder="单位" />
          </view>
        </view>
        <view v-else class="unknown-hint">数量暂不确定时不写入 0；保存后显示为数量未知。</view>
        <view class="form-section">
          <text class="field-label">备注（可选）</text>
          <textarea v-model="note" maxlength="300" placeholder="例如：放在冷藏室第二层" />
        </view>
      </view>
      <view v-if="error" class="error-copy">{{ error }}</view>
      <view class="preview-panel">
        <text class="preview-title">确认前预览</text>
        <view class="preview-row"><text>食材</text><text>{{ name.trim() || selected?.name || '未填写' }}</text></view>
        <view class="preview-row"><text>数量</text><text>{{ quantityStatus === 'known' ? `${quantity || '未填写'} ${unit || ''}`.trim() : '数量未知' }}</text></view>
        <view v-if="note.trim()" class="preview-row"><text>备注</text><text>{{ note.trim() }}</text></view>
      </view>
    </scroll-view>
    <view class="form-footer">
      <button class="cancel-button" :disabled="busy" @tap="cancel">取消</button>
      <button class="submit-button" :disabled="!canSubmit || busy || saved" :loading="busy" @tap="submit">{{ saved ? '已保存' : '保存库存' }}</button>
    </view>
    <BottomNav active="fridge" />
  </view>
</template>

<style scoped lang="scss">
.manual-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }
.target-banner { display: flex; flex-direction: column; gap: 5px; padding: 12px 16px; background: #eff6ff; color: #1e3a8a; font-size: 13px; line-height: 1.5; }
.target-label { font-weight: 600; }
.manual-scroll { flex: 1; min-height: 0; padding: 16px; padding-bottom: 8px; }
.form-panel, .preview-panel { margin-bottom: 14px; padding: 0 16px 16px; border-radius: 14px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }
.panel-title, .preview-title { display: block; padding: 17px 0 13px; border-bottom: 1px solid #e5e7eb; color: #18181b; font-size: 17px; font-weight: 600; }
.form-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 56px; border-bottom: 1px solid #e5e7eb; color: #27272a; font-size: 14px; }
.form-section { padding-top: 14px; }
.field-label { display: block; margin-bottom: 9px; color: #52525b; font-size: 14px; }
.form-section input, .form-section textarea { width: 100%; padding: 11px; border-radius: 8px; background: #f3f4f6; font-size: 14px; }
.form-section textarea { min-height: 76px; }
.segmented { display: flex; gap: 8px; }
.segmented text { flex: 1; padding: 11px 8px; border: 1px solid #d1d5db; border-radius: 8px; color: #52525b; text-align: center; font-size: 14px; }
.segmented .selected { border-color: #2563eb; background: #eff6ff; color: #1d4ed8; }
.quantity-field { display: flex; align-items: center; gap: 8px; }
.quantity-field input { width: 96px; padding: 9px; border-radius: 8px; background: #f3f4f6; text-align: right; }
.unknown-hint, .selected-hint { margin-top: 12px; padding: 11px; border-radius: 8px; background: #fef3c7; color: #92400e; font-size: 13px; line-height: 1.5; }
.selected-hint { background: #ecfdf5; color: #047857; }
.suggest-list { margin-top: 8px; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; }
.suggest-item { padding: 10px 12px; border-top: 1px solid #f4f4f5; background: #fff; }
.suggest-item:first-child { border-top: 0; }
.suggest-name, .suggest-meta { display: block; overflow-wrap: anywhere; }
.suggest-name { color: #18181b; font-size: 14px; font-weight: 600; }
.suggest-meta { margin-top: 4px; color: #71717a; font-size: 12px; }
.error-copy { margin: 0 2px 14px; color: #b91c1c; font-size: 13px; }
.preview-row { display: flex; justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid #e5e7eb; color: #52525b; font-size: 13px; }
.preview-row:last-child { border-bottom: 0; }
.preview-row text:last-child { max-width: 65%; color: #18181b; text-align: right; overflow-wrap: anywhere; }
.form-footer { display: flex; gap: 10px; padding: 12px 16px 16px; border-top: 1px solid #e5e7eb; background: #fff; }
.cancel-button, .submit-button { flex: 1; margin: 0; border-radius: 9px; font-size: 14px; line-height: 46px; }
.cancel-button { background: #e5e7eb; color: #27272a; }
.submit-button { background: #2563eb; color: #fff; }
.submit-button[disabled] { opacity: .5; }
.cancel-button::after, .submit-button::after { border: 0; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
</style>
