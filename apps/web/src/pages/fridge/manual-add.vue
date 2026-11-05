<script lang="ts">
import { defineComponent } from 'vue'
import SubpageHeader from '../../components/SubpageHeader.vue'
import BottomNav from '../../components/BottomNav.vue'
import { request } from '../../shared/transport'
import { session } from '../../shared/session'

export default defineComponent({
  name: 'FridgeManualAddPage',
  components: { SubpageHeader, BottomNav },
  data() { return { name: '', quantityStatus: 'unknown' as 'known' | 'unknown', quantity: '', unit: '个', note: '', error: '', busy: false, saved: false } },
  computed: {
    canSubmit(): boolean { return Boolean(this.name.trim()) && (this.quantityStatus === 'unknown' || (Boolean(this.quantity.trim()) && Number.isFinite(Number(this.quantity)) && Number(this.quantity) >= 0 && Boolean(this.unit.trim()))) },
  },
  methods: {
    selectQuantityStatus(value: 'known' | 'unknown') { this.quantityStatus = value; if (value === 'unknown') this.quantity = '' },
    async submit() {
      if (this.busy || this.saved) return
      this.error = ''
      if (!this.name.trim()) { this.error = '请填写食材名称'; return }
      if (!this.canSubmit) { this.error = '请填写有效数量，或选择数量未知'; return }
      if (!session.householdId) { this.error = '尚未选择家庭'; return }
      const householdId = session.householdId
      const payload = {
        name: this.name.trim(), quantity_status: this.quantityStatus,
        quantity: this.quantityStatus === 'known' ? this.quantity : null,
        unit: this.quantityStatus === 'known' ? this.unit : null, notes: this.note.trim(),
      }
      this.busy = true
      try {
        await request(`/households/${householdId}/inventory-lots`, payload)
        this.saved = true
        uni.showToast({ title: '已保存', icon: 'success' })
        setTimeout(() => { if (householdId === session.householdId) uni.navigateBack({ delta: 1 }) }, 400)
      } catch (error) { this.error = error instanceof Error ? error.message : '保存失败' } finally { this.busy = false }
    },
    cancel() { if (!this.busy) uni.navigateBack({ delta: 1 }) },
  },
})
</script>

<template>
  <view class="manual-page">
    <SubpageHeader title="手动新增库存" />
     <view class="target-banner"><text class="target-label">手动录入</text><text>数量未知时不会写入数量或单位。</text></view>
    <scroll-view scroll-y class="manual-scroll">
      <view class="form-panel"><text class="panel-title">食材信息</text><view class="form-row"><text>食材名称</text><input v-model="name" maxlength="80" placeholder="例如：鸡蛋、番茄" /></view><view class="form-section"><text class="field-label">数量状态</text><view class="segmented"><text :class="{ selected: quantityStatus === 'known' }" @tap="selectQuantityStatus('known')">已知数量</text><text :class="{ selected: quantityStatus === 'unknown' }" @tap="selectQuantityStatus('unknown')">数量未知</text></view></view><view v-if="quantityStatus === 'known'" class="form-row"><text>实际数量</text><view class="quantity-field"><input v-model="quantity" type="number" placeholder="输入数量" /><picker :range="['个', '盒', '袋', '瓶', '克', '千克']" @change="unit = ['个', '盒', '袋', '瓶', '克', '千克'][Number($event.detail.value)]"><text>{{ unit }}　⌄</text></picker></view></view><view v-else class="unknown-hint">数量暂不确定，保存后会显示为“数量未知 / 待核对”，不会按 0 处理。</view><view class="form-section"><text class="field-label">备注（可选）</text><textarea v-model="note" maxlength="200" placeholder="例如：放在冷藏室第二层" /></view></view>
      <view v-if="error" class="error-copy">{{ error }}</view>
      <view class="preview-panel"><text class="preview-title">确认前预览</text><view class="preview-row"><text>食材</text><text>{{ name.trim() || '未填写' }}</text></view><view class="preview-row"><text>数量</text><text>{{ quantityStatus === 'known' ? `${quantity || '未填写'} ${unit}` : '数量未知 / 待核对' }}</text></view><view v-if="note.trim()" class="preview-row"><text>备注</text><text>{{ note.trim() }}</text></view></view>
    </scroll-view>
     <view class="form-footer"><button class="cancel-button" :disabled="busy" @tap="cancel">取消</button><button class="submit-button" :disabled="!canSubmit || busy || saved" :loading="busy" @tap="submit">{{ saved ? '已保存' : '保存库存' }}</button></view>
    <BottomNav active="fridge" />
  </view>
</template>

<style scoped lang="scss">
.manual-page { min-height: 100vh; display: flex; flex-direction: column; background: #f3f4f6; }.target-banner { display: flex; flex-direction: column; gap: 5px; padding: 12px 16px; background: #fffbeb; color: #92400e; font-size: 13px; line-height: 1.5; }.target-label { font-weight: 600; }.manual-scroll { flex: 1; min-height: 0; padding: 16px; }.form-panel, .preview-panel { margin-bottom: 14px; padding: 0 16px 16px; border-radius: 14px; background: #fff; box-shadow: 0 4px 14px #18181b0d; }.panel-title, .preview-title { display: block; padding: 17px 0 13px; border-bottom: 1px solid #e5e7eb; color: #18181b; font-size: 17px; font-weight: 600; }.form-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 56px; border-bottom: 1px solid #e5e7eb; color: #27272a; font-size: 14px; }.form-row input { flex: 1; min-width: 0; text-align: right; }.form-section { padding-top: 14px; }.field-label { display: block; margin-bottom: 9px; color: #52525b; font-size: 14px; }.segmented { display: flex; gap: 8px; }.segmented text { flex: 1; padding: 11px 8px; border: 1px solid #d1d5db; border-radius: 8px; color: #52525b; text-align: center; font-size: 14px; }.segmented .selected { border-color: #2563eb; background: #eff6ff; color: #1d4ed8; }.quantity-field { display: flex; align-items: center; gap: 8px; }.quantity-field input { width: 90px; padding: 9px; border-radius: 8px; background: #f3f4f6; text-align: right; }.quantity-field picker { padding: 9px; border-radius: 8px; background: #f3f4f6; color: #374151; }.unknown-hint { margin-top: 12px; padding: 11px; border-radius: 8px; background: #fef3c7; color: #92400e; font-size: 13px; line-height: 1.5; }.form-section textarea { width: 100%; min-height: 76px; padding: 11px; border-radius: 8px; background: #f3f4f6; font-size: 14px; }.error-copy { margin: 0 2px 14px; color: #b91c1c; font-size: 13px; }.preview-panel { padding-bottom: 6px; }.preview-row { display: flex; justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid #e5e7eb; color: #52525b; font-size: 13px; }.preview-row:last-child { border-bottom: 0; }.preview-row text:last-child { max-width: 65%; color: #18181b; text-align: right; overflow-wrap: anywhere; }.form-footer { display: flex; gap: 10px; padding: 12px 16px 16px; border-top: 1px solid #e5e7eb; background: #fff; }.cancel-button, .submit-button { flex: 1; margin: 0; border-radius: 9px; font-size: 14px; line-height: 46px; }.cancel-button { background: #e5e7eb; color: #27272a; }.submit-button { background: #2563eb; color: #fff; }.submit-button[disabled] { opacity: .5; }.cancel-button::after, .submit-button::after { border: 0; }
</style>
