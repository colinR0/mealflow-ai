import { reactive } from 'vue'
import { request, requestLogin } from './transport'
import type { Household } from './types'

interface StoredSession { userId: string; csrfToken: string; accessToken: string; householdId: string }
interface LoginResult { user_id: string; csrf_token: string; access_token?: string }

const SESSION_KEY = 'mealflow.session.v1'
export const session = reactive({
  userId: '',
  csrfToken: '',
  accessToken: '',
  householdId: '',
  households: [] as Household[],
  restored: false,
})

function persist() {
  const data: StoredSession = { userId: session.userId, csrfToken: session.csrfToken, accessToken: session.accessToken, householdId: session.householdId }
  uni.setStorageSync(SESSION_KEY, data)
}

function clear() {
  session.userId = ''
  session.csrfToken = ''
  session.accessToken = ''
  session.householdId = ''
  session.households = []
  uni.removeStorageSync(SESSION_KEY)
}

export async function restoreSession() {
  const saved = uni.getStorageSync(SESSION_KEY) as StoredSession | ''
  if (saved) {
    session.userId = saved.userId
    session.csrfToken = saved.csrfToken
    session.accessToken = saved.accessToken || ''
    session.householdId = saved.householdId
    await request<{ user_id: string }>('/auth/me')
      .then(() => loadHouseholds())
      .catch(() => clear())
  }
  session.restored = true
}

export async function login(phone: string, password: string) {
  const result = await requestLogin<LoginResult>('/auth/login', { phone, password })
  session.userId = result.user_id
  session.csrfToken = result.csrf_token
  session.accessToken = result.access_token || ''
  persist()
  await loadHouseholds()
}

export async function logout() {
  return request('/auth/logout', {}).then(clear, clear)
}

export async function loadHouseholds() {
  session.households = await request<Household[]>('/households')
  if (!session.households.some((household) => household.id === session.householdId)) {
    session.householdId = session.households[0]?.id || ''
  }
  persist()
}

export function selectHousehold(id: string) {
  session.householdId = id
  persist()
}
