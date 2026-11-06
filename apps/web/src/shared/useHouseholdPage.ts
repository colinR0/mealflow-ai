import { reactive } from 'vue'
import { request } from './transport'
import { session } from './session'

export function useHouseholdPage<T>(path: (householdId: string) => string) {
  const state = reactive({ items: [] as T[], loading: false, error: '', sequence: 0 }) as { items: T[]; loading: boolean; error: string; sequence: number }
  async function refresh() {
    const sequence = ++state.sequence
    state.items = []
    state.error = ''
    state.loading = false
    if (!session.restored || !session.householdId) return
    state.loading = true
    await request<T[] | { items: T[] }>(path(session.householdId))
      .then((page) => { if (sequence === state.sequence) state.items = Array.isArray(page) ? page : page.items })
      .catch((error: unknown) => { if (sequence === state.sequence) state.error = error instanceof Error ? error.message : '加载失败' })
      .finally(() => { if (sequence === state.sequence) state.loading = false })
  }
  return { state, refresh }
}
