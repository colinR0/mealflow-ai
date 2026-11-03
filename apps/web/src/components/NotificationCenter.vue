<script lang="ts">
import { defineComponent } from 'vue'
import { Bell, CircleCheck, Clock, Warning } from '@element-plus/icons-vue'

import { fetchHousehold, type Household } from '../api/chat'
import { fetchInventory, type InventoryItem } from '../api/inventory'
import { fetchProcurement, type ProcurementItem } from '../composables/procurement'

interface Notice {
  id: string
  title: string
  detail: string
  time: string
  tone: 'danger' | 'warning' | 'info' | 'success'
  icon: typeof Bell
}

const SEEN_KEY = 'mealflow_notification_seen_v1'
const INVENTORY_DELIVERY_KEY = 'mealflow_inventory_notification_delivery_v1'
const DAY_MS = 24 * 60 * 60 * 1000

interface InventoryDeliveryState {
  lastSentAt: number
  noticeId: string
}

export default defineComponent({
  name: 'NotificationCenter',
  components: { Bell, CircleCheck, Clock, Warning },
  data() {
    return {
      notices: [] as Notice[],
      seenIds: [] as string[],
      loading: false,
      popoverOpen: false,
      refreshTimer: undefined as ReturnType<typeof setInterval> | undefined,
    }
  },
  computed: {
    unreadNotices(): Notice[] {
      return this.notices.filter((notice) => !this.seenIds.includes(notice.id))
    },
    unreadCount(): number {
      return this.unreadNotices.length
    },
    latestUnread(): Notice | null {
      return this.unreadNotices[0] ?? null
    },
  },
  methods: {
    loadSeen() {
      try {
        const stored = JSON.parse(localStorage.getItem(SEEN_KEY) ?? '[]')
        this.seenIds = Array.isArray(stored)
          ? stored.filter((id): id is string => typeof id === 'string')
          : []
      } catch {
        this.seenIds = []
      }
    },

    saveSeen() {
      localStorage.setItem(SEEN_KEY, JSON.stringify(this.seenIds.slice(-300)))
    },

    readInventoryDelivery(): Record<string, InventoryDeliveryState> {
      try {
        const value = JSON.parse(localStorage.getItem(INVENTORY_DELIVERY_KEY) ?? '{}')
        return value && typeof value === 'object'
          ? (value as Record<string, InventoryDeliveryState>)
          : {}
      } catch {
        return {}
      }
    },

    saveInventoryDelivery(value: Record<string, InventoryDeliveryState>) {
      localStorage.setItem(INVENTORY_DELIVERY_KEY, JSON.stringify(value))
    },

    reconcileSeen(activeNotices: Notice[]) {
      const activeIds = new Set(activeNotices.map((notice) => notice.id))
      const next = this.seenIds.filter((id) => activeIds.has(id))
      if (next.length !== this.seenIds.length) {
        this.seenIds = next
        this.saveSeen()
      }
    },

    daysUntil(value: string | null): number | null {
      if (!value) return null
      const today = new Date()
      const start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
      const expiry = new Date(`${value}T00:00:00`).getTime()
      return Math.round((expiry - start) / 86_400_000)
    },

    buildNotices(
      inventory: InventoryItem[],
      household: Household | null,
      procurement: ProcurementItem[],
    ): Notice[] {
      const next: Notice[] = []
      const delivery = this.readInventoryDelivery()
      const activeDeliveryKeys = new Set<string>()
      let deliveryChanged = false
      const now = Date.now()
      for (const item of inventory) {
        const days = this.daysUntil(item.expires_on)
        if (days === null || days > 2) continue

        if (days < 0) {
          const key = `inventory:expired:${item.id}`
          activeDeliveryKeys.add(key)
          const state = delivery[key]
          if (!state) {
            delivery[key] = { lastSentAt: now, noticeId: key }
            deliveryChanged = true
          }
          next.push({
            id: key,
            title: `${item.ingredient_name} 已过期`,
            detail: `${item.quantity}${item.unit} · 到期日 ${item.expires_on} · 仅提醒一次`,
            time: '冰箱库存',
            tone: 'danger',
            icon: Warning,
          })
          continue
        }

        const key = `inventory:expiring:${item.id}`
        activeDeliveryKeys.add(key)
        const state = delivery[key]
        if (!state || now - state.lastSentAt >= DAY_MS) {
          delivery[key] = {
            lastSentAt: now,
            noticeId: `${key}:${now}`,
          }
          deliveryChanged = true
        }
        next.push({
          id: delivery[key].noticeId,
          title: `${item.ingredient_name} 即将到期`,
          detail: `${item.quantity}${item.unit} · ${days === 0 ? '今天到期' : `${days} 天后到期`} · 每 24 小时提醒一次`,
          time: '冰箱库存',
          tone: 'warning',
          icon: Warning,
        })
      }
      for (const key of Object.keys(delivery)) {
        if (!activeDeliveryKeys.has(key)) {
          delete delivery[key]
          deliveryChanged = true
        }
      }
      if (deliveryChanged) this.saveInventoryDelivery(delivery)
      for (const member of household?.members ?? []) {
        for (const restriction of member.restrictions) {
          next.push({
            id: `restriction:${restriction.id}`,
            title: `${member.display_name}新增${restriction.type === 'allergy' ? '过敏红线' : '长期忌口'}`,
            detail: `${restriction.type === 'allergy' ? '强制过滤' : '规划时优先规避'} · ${restriction.ingredient_name}`,
            time: '人员信息',
            tone: restriction.type === 'allergy' ? 'danger' : 'warning',
            icon: restriction.type === 'allergy' ? Warning : Clock,
          })
        }
      }
      for (const event of household?.events ?? []) {
        const today = new Date().toISOString().slice(0, 10)
        if (event.end_date < today) continue
        next.push({
          id: `event:${event.id}`,
          title: `${event.member_name}有${event.event_type}事件`,
          detail: `${event.constraint_tags.join('、') || '请关注饮食调整'} · ${event.start_date} 至 ${event.end_date}`,
          time: '人员信息',
          tone: 'info',
          icon: Clock,
        })
      }
      for (const item of procurement.filter((current) => !current.checked)) {
        next.push({
          id: `procurement:${item.id}`,
          title: `待采购：${item.ingredient}`,
          detail: `缺 ${item.quantity}${item.unit} · ${item.source === 'agent' ? 'Agent 方案生成' : '手动添加'}`,
          time: '采购清单',
          tone: 'success',
          icon: CircleCheck,
        })
      }
      return next
    },

    async refresh() {
      this.loading = true
      try {
        const [inventory, household, procurement] = await Promise.allSettled([
          fetchInventory(),
          fetchHousehold(),
          fetchProcurement(),
        ])
        const next = this.buildNotices(
          inventory.status === 'fulfilled' ? inventory.value : [],
          household.status === 'fulfilled' ? household.value : null,
          procurement.status === 'fulfilled' ? procurement.value : [],
        )
        this.notices = next
        this.reconcileSeen(next)
      } finally {
        this.loading = false
      }
    },

    markSeen(notice: Notice) {
      if (!this.seenIds.includes(notice.id)) {
        this.seenIds.push(notice.id)
        this.saveSeen()
      }
    },

    markAllSeen() {
      this.seenIds = Array.from(
        new Set([...this.seenIds, ...this.notices.map((notice) => notice.id)]),
      )
      this.saveSeen()
    },

    onDataUpdated() {
      void this.refresh()
    },
  },
  mounted() {
    this.loadSeen()
    void this.refresh()
    this.refreshTimer = setInterval(() => void this.refresh(), 30_000)
    window.addEventListener('mealflow:inventory-updated', this.onDataUpdated)
    window.addEventListener('mealflow:procurement-updated', this.onDataUpdated)
    window.addEventListener('mealflow:procurement-generated', this.onDataUpdated)
  },
  beforeUnmount() {
    clearInterval(this.refreshTimer)
    window.removeEventListener('mealflow:inventory-updated', this.onDataUpdated)
    window.removeEventListener('mealflow:procurement-updated', this.onDataUpdated)
    window.removeEventListener('mealflow:procurement-generated', this.onDataUpdated)
  },
})
</script>

<template>
  <div class="notification-center">
    <el-popover
      v-model:visible="popoverOpen"
      placement="bottom-end"
      :width="390"
      trigger="click"
      popper-class="notification-popper"
    >
      <template #reference>
        <button class="notification-trigger" type="button" aria-label="消息中心">
          <el-icon><Bell /></el-icon>
          <span v-if="unreadCount" class="notification-badge">{{
            unreadCount > 99 ? '99+' : unreadCount
          }}</span>
        </button>
      </template>
      <div class="notification-panel">
        <div class="notification-head">
          <div>
            <strong>消息中心</strong>
            <small>{{ unreadCount ? `${unreadCount} 条未读提醒` : '暂时没有未读提醒' }}</small>
          </div>
          <el-button v-if="unreadCount" size="small" text @click="markAllSeen">全部已读</el-button>
        </div>
        <div v-loading="loading" class="notification-list">
          <button
            v-for="notice in unreadNotices"
            :key="notice.id"
            class="notification-item"
            :class="[{ unread: !seenIds.includes(notice.id) }, notice.tone]"
            type="button"
            @click="markSeen(notice)"
          >
            <span class="notification-icon"
              ><el-icon><component :is="notice.icon" /></el-icon
            ></span>
            <span class="notification-copy">
              <strong>{{ notice.title }}</strong>
              <small>{{ notice.detail }}</small>
              <em>{{ notice.time }}</em>
            </span>
            <i v-if="!seenIds.includes(notice.id)" class="unread-dot"></i>
          </button>
          <div v-if="!loading && !unreadNotices.length" class="notification-empty">
            <el-icon><CircleCheck /></el-icon>
            <span>库存、人员和采购清单都很安心</span>
          </div>
        </div>
      </div>
    </el-popover>
    <span v-if="latestUnread" class="notification-summary" :title="latestUnread.detail">
      {{ latestUnread.title }}
    </span>
  </div>
</template>

<style>
.notification-popper {
  padding: 0 !important;
  border: 1px solid #dbe7e1 !important;
  border-radius: 16px !important;
  overflow: hidden;
}
.notification-panel {
  background: #fff;
}
.notification-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 17px 12px;
  border-bottom: 1px solid #edf1ee;
}
.notification-head div {
  display: grid;
  gap: 3px;
}
.notification-head strong {
  color: var(--ink);
  font-size: 15px;
}
.notification-head small {
  color: var(--muted);
  font-size: 11px;
}
.notification-list {
  max-height: 390px;
  overflow-y: auto;
  padding: 5px;
}
.notification-item {
  position: relative;
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 11px 10px;
  border: 0;
  border-radius: 11px;
  background: transparent;
  color: var(--ink);
  text-align: left;
}
.notification-item:hover {
  background: #f5f9f6;
}
.notification-item.unread {
  background: #f7fbf8;
}
.notification-icon {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 9px;
  background: #e9f3ef;
  color: var(--green);
}
.notification-item.danger .notification-icon {
  background: #fdeceb;
  color: #b34c43;
}
.notification-item.warning .notification-icon {
  background: #fff5df;
  color: #a56d18;
}
.notification-item.info .notification-icon {
  background: #edf3f6;
  color: #568094;
}
.notification-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
  flex: 1;
}
.notification-copy strong {
  font-size: 12.5px;
}
.notification-copy small {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
}
.notification-copy em {
  color: #a0ada8;
  font-size: 10px;
  font-style: normal;
}
.unread-dot {
  width: 6px;
  height: 6px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--green-2);
}
.notification-empty {
  display: grid;
  place-items: center;
  gap: 7px;
  padding: 44px 15px;
  color: var(--muted);
  font-size: 12px;
}
.notification-empty .el-icon {
  color: #76aa92;
  font-size: 24px;
}
</style>

<style scoped>
.notification-center {
  display: flex;
  align-items: center;
  gap: 9px;
  max-width: 350px;
}
.notification-trigger {
  position: relative;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: 1px solid var(--border, #d8ddd7);
  border-radius: 50%;
  background: #fff;
  color: var(--green);
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(33, 60, 54, 0.08);
}
.notification-trigger:hover {
  border-color: #bcd3cc;
  background: #f7fbf8;
}
.notification-trigger .el-icon {
  font-size: 18px;
}
.notification-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  display: grid;
  place-items: center;
  border: 2px solid #f6f5ef;
  border-radius: 999px;
  background: #c44e43;
  color: #fff;
  font-size: 10px;
  font-weight: 750;
  line-height: 1;
}
.notification-summary {
  max-width: 220px;
  overflow: hidden;
  padding: 7px 11px;
  border: 1px solid #eadfbd;
  border-radius: 999px;
  background: #fffaf0;
  color: #8d671d;
  font-size: 11px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
