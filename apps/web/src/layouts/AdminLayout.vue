<script lang="ts">
import { defineComponent } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Fold,
  Expand,
  Dish,
  Food,
  User,
  Timer,
  Setting,
  MagicStick,
  House,
  ShoppingCart,
  EditPen,
} from '@element-plus/icons-vue'
import AccountChip from '../components/AccountChip.vue'
import NotificationCenter from '../components/NotificationCenter.vue'
import {
  conversationStarted as started,
  dateLine as currentDateLine,
  greeting as currentGreeting,
} from '../composables/greeting'

// 已开发菜单:选中即跳转;占位菜单:点击提示功能未开发
const allMenus: Array<{ label: string; icon: unknown; index?: string }> = [
  { index: '/admin/agent', label: '智能配餐', icon: Dish },
  { index: '/admin/inventory', label: '冰箱库存', icon: Food },
  { index: '/admin/procurement', label: '采购清单', icon: ShoppingCart },
  { index: '/admin/cooking-history', label: '下厨足迹', icon: Timer },
  { index: '/admin/members', label: '人员信息', icon: User },
  { index: '/admin/cookbook', label: '懒人宝典', icon: MagicStick },
  { index: '/admin/code-practice', label: '代码练兵场', icon: EditPen },
  { label: '家庭档案', icon: House },
  { label: '系统设置', icon: Setting },
]

export default defineComponent({
  name: 'AdminLayout',
  components: { AccountChip, NotificationCenter, Fold, Expand },
  data() {
    return {
      collapsed: false,
      allMenus,
      Fold,
      Expand,
      Dish,
      Food,
      User,
      Timer,
      Setting,
      MagicStick,
      House,
      ShoppingCart,
      EditPen,
    }
  },
  computed: {
    routePath(): string {
      return this.$route.path
    },
    conversationStarted(): boolean {
      return started.value
    },
    dateLine(): string {
      return currentDateLine
    },
    greeting(): string {
      return currentGreeting
    },
  },
  methods: {
    onUserCommand(command: string) {
      if (command === 'version') {
        ElMessage({ message: '当前版本 v1.2', type: 'info', grouping: true })
      } else {
        ElMessage.info('该功能未开发,敬请期待 ✨')
      }
    },
    onMenuSelect(index: string) {
      const target = this.allMenus.find((menu) => menu.index === index)
      if (target?.index) this.$router.push(target.index)
      else ElMessage.info('该功能未开发,敬请期待 ✨')
    },
  },
})
</script>

<template>
  <div class="admin-shell" :class="{ collapsed }">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">食</span>
        <span class="brand-text">
          <strong>食序 MealFlow</strong>
          <small>家庭智能配餐 Agent</small>
        </span>
      </div>
      <el-menu :default-active="routePath" class="side-menu" @select="onMenuSelect">
        <el-menu-item
          v-for="menu in allMenus"
          :key="menu.label"
          :index="menu.index ?? `placeholder-${menu.label}`"
        >
          <el-icon><component :is="menu.icon" /></el-icon>
          <span class="menu-label">{{ menu.label }}</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-foot">
        <button
          class="collapse-btn"
          :title="collapsed ? '展开' : '收起'"
          @click="collapsed = !collapsed"
        >
          <el-icon v-if="!collapsed"><Fold /></el-icon>
          <el-icon v-else><Expand /></el-icon>
        </button>
        <div class="sidebar-note">
          <span class="status-dot"></span>
          <span class="note-text">食不厌精，问不厌详</span>
        </div>
      </div>
    </aside>
    <main class="admin-main">
      <header class="topbar">
        <div class="greeting" :class="{ visible: conversationStarted }" aria-live="polite">
          <small>{{ dateLine }}</small>
          <h1>{{ greeting }}</h1>
        </div>
      </header>
      <RouterView v-slot="{ Component }">
        <!-- 对话工作台在切换页面时保留会话与方案 -->
        <KeepAlive include="ConversationWorkbench">
          <component :is="Component" />
        </KeepAlive>
      </RouterView>
    </main>

    <!-- 全系统共用的账户入口:固定在视口右上角,不随页面滚动 -->
    <div class="account-dock">
      <NotificationCenter />
      <AccountChip @command="onUserCommand" />
    </div>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  height: 100vh;
  min-height: 100vh;
  overflow: hidden;
}
.sidebar {
  width: 208px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #123f39;
  color: #fff;
  transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}
.admin-shell.collapsed .sidebar {
  width: 64px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 12px;
  overflow: hidden;
  white-space: nowrap;
}
.brand-mark {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 10px;
  background: #e5bd68;
  color: #18322f;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}
/* 折叠态:文字用 opacity+transform 淡出向左,不再参与宽度布局,避免裁切与抖动 */
.admin-shell.collapsed .brand {
  gap: 0;
  justify-content: center;
  padding: 16px 8px;
}
.admin-shell.collapsed .brand-mark {
  transform: scale(1.06);
}
.brand-text {
  display: inline-grid;
  gap: 3px;
  transition:
    opacity 0.2s ease,
    width 0.28s cubic-bezier(0.4, 0, 0.2, 1),
    transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
  overflow: hidden;
}
.admin-shell.collapsed .brand-text {
  opacity: 0;
  width: 0;
  transform: translateX(-10px) scale(0.9);
}
.brand strong {
  display: block;
  font-size: 15px;
  white-space: nowrap;
}
.brand small {
  display: block;
  font-size: 11px;
  opacity: 0.65;
  white-space: nowrap;
}
.side-menu {
  border-right: 0;
  background: transparent;
  flex: 1;
}
.side-menu:not(.el-menu--collapse) {
  width: 100%;
}
.side-menu :deep(.el-menu-item) {
  color: rgba(255, 255, 255, 0.78);
  white-space: nowrap;
}
.side-menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: rgba(255, 255, 255, 0.12);
}
.side-menu :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.08);
}
.menu-label {
  transition: opacity 0.2s ease;
  white-space: nowrap;
}
.admin-shell.collapsed .side-menu :deep(.el-menu-item) {
  justify-content: center;
  padding: 0;
}
.admin-shell.collapsed .side-menu :deep(.el-icon) {
  margin-right: 0;
}
.admin-shell.collapsed .menu-label {
  opacity: 0;
  display: inline-block;
  width: 0;
  overflow: hidden;
}
.sidebar-foot {
  padding: 12px;
  margin-top: auto;
}
.admin-shell.collapsed .sidebar-foot {
  padding: 12px 8px;
}
.collapse-btn {
  width: 100%;
  border: 0;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  border-radius: 8px;
  padding: 7px 0;
  cursor: pointer;
}
.collapse-btn:hover {
  background: rgba(255, 255, 255, 0.16);
}
.sidebar-note {
  font-size: 11px;
  opacity: 0.65;
  margin-top: 10px;
  white-space: nowrap;
  overflow: hidden;
  transition: opacity 0.2s ease;
}
.admin-shell.collapsed .sidebar-note {
  opacity: 0;
}
.status-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7fd1ae;
  margin-right: 6px;
}
.admin-main {
  flex: 1;
  min-width: 0;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  padding: 18px 22px 46px;
  background: #f6f5ef;
}
.topbar {
  position: relative;
  height: 40px;
  min-height: 40px;
  margin-bottom: 14px;
  padding-right: 132px;
}
.greeting {
  position: absolute;
  inset: 0 auto auto 0;
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.18s ease;
}
.greeting.visible {
  visibility: visible;
  opacity: 1;
}
.greeting small {
  color: #8a9a94;
  letter-spacing: 0.04em;
}
.greeting h1 {
  margin: 1px 0 0;
  font-size: 21px;
  line-height: 1.15;
  font-family: Georgia, 'Songti SC', serif;
}
/* 账户入口固定在视口右上角:全系统唯一一份,不随内容与滚动移动 */
.account-dock {
  position: fixed;
  top: 16px;
  right: 22px;
  z-index: 2000;
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
