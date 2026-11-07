import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from '../../apps/web/node_modules/typescript/lib/typescript.js'

class ApiError extends Error {
  constructor(message, statusCode) { super(message); this.statusCode = statusCode }
}

function loadPage(request, responses) {
  const source = readFileSync(new URL('../../apps/web/src/pages/fridge/inventory.vue', import.meta.url), 'utf8')
    .match(/<script lang="ts">([\s\S]*?)<\/script>/)[1]
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } })
  const exports = {}
  const modals = []
  runInNewContext(outputText, {
    exports, Error, setTimeout, clearTimeout,
    uni: { showModal: (options) => { modals.push(options); options.success({ confirm: true }) }, navigateTo: () => {} },
    require: (name) => {
      if (name === 'vue') return { defineComponent: (options) => options }
      if (name.endsWith('/session')) return { session: { householdId: 'test-household', households: [{ id: 'test-household', name: 'QA only' }] } }
      if (name.endsWith('/transport')) return { request, ApiError }
      if (name.endsWith('.vue')) return { default: {} }
      throw new Error(`Unexpected import: ${name}`)
    },
  })
  const component = exports.default
  const page = component.data()
  for (const [key, method] of Object.entries(component.methods)) page[key] = method.bind(page)
  for (const [key, computed] of Object.entries(component.computed || {})) Object.defineProperty(page, key, { get: () => computed.call(page) })
  Object.assign(page, responses)
  return { page, modals }
}

const lot = (version = 1) => ({ id: 'qa-lot', name: 'Milk', version, quantity_status: 'unknown', notes: '', status: 'active', confirmed_at: '2026-11-07T00:00:00+00:00' })

test('repeat transition triggers only one request before confirmation', async () => {
  let calls = 0
  let finish
  const pending = new Promise((resolve) => { finish = resolve })
  const { page } = loadPage(async () => { calls++; await pending; return { items: [], total: 0, offset: 0, has_more: false } })
  page.items = [lot()]
  const first = page.transition(page.items[0], 'consume')
  await new Promise((resolve) => setImmediate(resolve))
  const second = page.transition(page.items[0], 'consume')
  await second
  assert.equal(calls, 1)
  finish()
  await first
  assert.equal(page.busyId, '')
})

test('each quantity filter reaches API, not only unknown', async () => {
  const urls = []
  const { page } = loadPage(async (url) => { urls.push(url); return { items: [], total: 0, offset: 0, has_more: false } })
  for (const filter of ['all', 'known', 'unknown']) {
    page.filter = filter
    page.loading = false
    await page.load()
  }
  assert.deepEqual(urls.map((url) => new URL(url, 'http://local').searchParams.get('quantity_status')), [null, 'known', 'unknown'])
})

test('declining stale-version confirmation does not write stale or latest data', async () => {
  let writes = 0
  const { page } = loadPage(async (_url, payload) => {
    if (payload) { writes++; throw new ApiError('stale', 409) }
    return { items: [lot(4)], total: 1, offset: 0, has_more: false }
  })
  page.items = [lot()]
  page.confirmConflict = async () => false
  await page.transition(page.items[0], 'consume')
  assert.equal(writes, 1)
  assert.equal(page.error, '')
  assert.equal(page.busyId, '')
})




