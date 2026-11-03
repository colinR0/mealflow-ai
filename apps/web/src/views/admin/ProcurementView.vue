<script lang="ts">
import { defineComponent } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus, ShoppingCart } from '@element-plus/icons-vue'
import {
  createProcurementItem,
  deleteProcurementItem,
  fetchProcurement,
  type ProcurementItem,
  updateProcurementItem,
} from '../../composables/procurement'

export default defineComponent({
  name: 'ProcurementView',
  components: { ShoppingCart },
  data() {
    return {
      items: [] as ProcurementItem[],
      showAddDialog: false,
      submitting: false,
      Delete,
      Plus,
      ShoppingCart,
      form: { ingredient: '', quantity: '', unit: '份' },
    }
  },
  computed: {
    pendingItems(): ProcurementItem[] {
      return this.items.filter((item) => !item.checked)
    },
    completedItems(): ProcurementItem[] {
      return this.items.filter((item) => item.checked)
    },
  },
  async mounted() {
    try {
      this.items = await fetchProcurement()
    } catch {
      ElMessage.error('采购清单加载失败，请检查后端连接')
    }
  },
  methods: {
    openAdd() {
      this.form = { ingredient: '', quantity: '', unit: '份' }
      this.showAddDialog = true
    },
    async addManual() {
      if (!this.form.ingredient.trim()) return ElMessage.warning('请填写采购食材')
      this.submitting = true
      try {
        const created = await createProcurementItem({
          ingredient: this.form.ingredient.trim(),
          quantity: this.form.quantity.trim() || '—',
          unit: this.form.unit.trim() || '份',
          checked: false,
          source: 'manual',
        })
        this.items.unshift(created)
        this.showAddDialog = false
        ElMessage.success('已加入采购清单')
      } finally {
        this.submitting = false
      }
    },
    async toggle(item: ProcurementItem) {
      try {
        const checked = !item.checked
        await updateProcurementItem(item.id, { checked })
        item.checked = checked
      } catch {
        ElMessage.error('采购状态更新失败')
      }
    },
    async remove(item: ProcurementItem) {
      try {
        await ElMessageBox.confirm(`确定移除「${item.ingredient}」吗？`, '删除采购项', {
          type: 'warning',
          confirmButtonText: '移除',
          cancelButtonText: '取消',
        })
      } catch {
        return
      }
      await deleteProcurementItem(item.id)
      this.items = this.items.filter((current) => current.id !== item.id)
    },
    async clearCompleted() {
      await Promise.all(this.completedItems.map((item) => deleteProcurementItem(item.id)))
      this.items = this.items.filter((item) => !item.checked)
      ElMessage.success('已清理已购买项目')
    },
  },
})
</script>

<template>
  <section class="procurement-page">
    <header class="page-hero">
      <div>
        <span class="eyebrow">SMART SHOPPING LIST</span>
        <h2>采购清单</h2>
        <p>Agent 会把方案里的冰箱缺口整理到这里，也可以随手补充想买的食材。</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openAdd">手动添加</el-button>
    </header>

    <div class="summary-strip">
      <div>
        <span>待采购</span><strong>{{ pendingItems.length }}</strong
        ><small>项</small>
      </div>
      <div>
        <span>已购买</span><strong>{{ completedItems.length }}</strong
        ><small>项</small>
      </div>
      <div>
        <span>清单总计</span><strong>{{ items.length }}</strong
        ><small>项</small>
      </div>
      <div><span>来源</span><strong>AI</strong><small>+ 手动</small></div>
    </div>

    <div class="procurement-list-wrap">
      <div class="list-heading">
        <div>
          <strong>待采购食材</strong>
          <small>勾选后会移动到已购买</small>
        </div>
        <el-button v-if="completedItems.length" size="small" text @click="clearCompleted"
          >清理已购买</el-button
        >
      </div>

      <div v-if="pendingItems.length" class="shopping-list">
        <div v-for="item in pendingItems" :key="item.id" class="shopping-item">
          <el-checkbox :model-value="item.checked" @change="toggle(item)">
            <span class="shopping-name">{{ item.ingredient }}</span>
          </el-checkbox>
          <span class="shopping-quantity">缺 {{ item.quantity }}{{ item.unit }}</span>
          <el-tag size="small" :type="item.source === 'agent' ? 'success' : 'info'" effect="plain">
            {{ item.source === 'agent' ? 'Agent 推荐' : '手动添加' }}
          </el-tag>
          <el-button :icon="Delete" size="small" text type="danger" @click="remove(item)" />
        </div>
      </div>
      <div v-else class="empty-state procurement-empty">
        <el-icon><ShoppingCart /></el-icon>
        <h3>采购清单还是空的</h3>
        <p>在智能配餐中确认方案，或手动添加需要购买的食材。</p>
        <el-button type="primary" @click="openAdd">添加第一项</el-button>
      </div>

      <div v-if="completedItems.length" class="completed-section">
        <div class="list-heading">
          <strong>已购买</strong><small>{{ completedItems.length }} 项</small>
        </div>
        <div class="shopping-list completed-list">
          <div v-for="item in completedItems" :key="item.id" class="shopping-item checked">
            <el-checkbox :model-value="item.checked" @change="toggle(item)">
              <span class="shopping-name">{{ item.ingredient }}</span>
            </el-checkbox>
            <span class="shopping-quantity">{{ item.quantity }}{{ item.unit }}</span>
            <el-button :icon="Delete" size="small" text type="danger" @click="remove(item)" />
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="showAddDialog" title="手动添加采购项" width="440px" destroy-on-close>
      <el-form label-position="top" @submit.prevent="addManual">
        <el-form-item label="食材名称" required>
          <el-input v-model="form.ingredient" maxlength="30" placeholder="例如：橄榄油、香菜" />
        </el-form-item>
        <div class="form-grid two">
          <el-form-item label="数量">
            <el-input v-model="form.quantity" placeholder="例如：1" />
          </el-form-item>
          <el-form-item label="单位">
            <el-input v-model="form.unit" maxlength="8" placeholder="例如：份、瓶、kg" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="addManual">加入清单</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.procurement-page {
  max-width: 1180px;
  width: 100%;
  height: calc(100vh - 118px);
  min-height: 0;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.procurement-list-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 4px 28px 0;
  scrollbar-gutter: stable;
}
.list-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.list-heading div {
  display: grid;
  gap: 3px;
}
.list-heading small {
  color: var(--muted);
  font-size: 12px;
}
.shopping-list {
  display: grid;
  gap: 8px;
}
.shopping-item {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 58px;
  padding: 10px 13px;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: #fff;
  box-shadow: 0 4px 15px rgba(33, 60, 54, 0.035);
}
.shopping-item:hover {
  border-color: #b9d2c8;
  box-shadow: 0 8px 22px rgba(33, 60, 54, 0.08);
}
.shopping-item :deep(.el-checkbox) {
  flex: 1;
  min-width: 180px;
  margin-right: auto;
}
.shopping-name {
  color: var(--ink);
  font-weight: 650;
}
.shopping-quantity {
  color: var(--muted);
  font-size: 13px;
  white-space: nowrap;
}
.shopping-item.checked {
  opacity: 0.62;
  background: #f7f9f6;
}
.shopping-item.checked .shopping-name {
  text-decoration: line-through;
}
.completed-section {
  margin-top: 28px;
}
.completed-list {
  opacity: 0.9;
}
.procurement-empty {
  margin-top: 20px;
}
</style>
