import { createRouter, createWebHistory } from 'vue-router'

import AdminLayout from '../layouts/AdminLayout.vue'
import ConversationWorkbench from '../views/admin/ConversationWorkbench.vue'
import MembersView from '../views/admin/MembersView.vue'
import InventoryView from '../views/admin/InventoryView.vue'
import CookingHistoryView from '../views/admin/CookingHistoryView.vue'
import ProcurementView from '../views/admin/ProcurementView.vue'
import LazyCookbookView from '../views/admin/LazyCookbookView.vue'
import CodePracticeView from '../views/admin/CodePracticeView.vue'
import LoginView from '../views/LoginView.vue'
import DurableWorkbench from '../views/admin/DurableWorkbench.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/admin/agent' },
    { path: '/login', component: LoginView },
    {
      path: '/admin',
      component: AdminLayout,
      meta: { requiresAuth: true },
      children: [
        { path: 'agent', component: ConversationWorkbench },
        { path: 'durable-agent', component: DurableWorkbench },
        { path: 'members', component: MembersView },
        { path: 'inventory', component: InventoryView },
        { path: 'cooking-history', component: CookingHistoryView },
        { path: 'procurement', component: ProcurementView },
        { path: 'cookbook', component: LazyCookbookView },
        { path: 'code-practice', component: CodePracticeView },
      ],
    },
  ],
})

// 登录守卫(口子):当前为演示模式直接放行;登录功能上线后校验 token 再放行。
router.beforeEach((to) => {
  if (!to.meta.requiresAuth) return true
  // TODO(V2.1): const token = localStorage.getItem('mealflow_token'); if (!token) return '/login'
  return true
})

export default router
